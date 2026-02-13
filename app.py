"""
Real Estate Panorama Plot Marker - Flask Application
Uses Supabase for Auth + DB. All data is user-scoped; admin can grant client/viewer access.
"""
import os
import json
import uuid
import time
from datetime import datetime

from dotenv import load_dotenv
_DOTENV_PATH = os.path.join(os.path.dirname(__file__), '.env')
try:
    load_dotenv(dotenv_path=_DOTENV_PATH)
except Exception:
    # If dotenv discovery/load fails for any reason, rely on real env vars.
    pass

# Fix SSL certificate errors (e.g. "self-signed certificate in certificate chain" on macOS)
if os.environ.get('SUPABASE_SSL_VERIFY', 'true').lower() in ('false', '0', 'no'):
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
except ImportError:
    pass

from flask import Flask, render_template, request, jsonify, send_from_directory, redirect
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from PIL import Image
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:
    boto3 = None
    BotoConfig = None

RESAMPLE_LANCZOS = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
EXIF_ORIENTATION_TAG = 274  # 0x0112 (Orientation)

from db import (
    get_supabase,
    get_current_user,
    require_auth,
    require_admin,
    get_panorama_with_access,
    get_panorama_by_id,
    list_panoramas_for_user,
    can_edit_plots,
    can_delete_panorama,
    get_profile,
    ensure_profile,
)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_UPLOAD_BYTES', str(50 * 1024 * 1024)))  # 50MB
app.config['DATABASE'] = 'panorama.db'

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
SUPABASE_S3_ENDPOINT = os.environ.get(
    'SUPABASE_S3_ENDPOINT',
    'https://qavugigprqbnslywkmri.storage.supabase.co/storage/v1/s3',
).rstrip('/')
SUPABASE_S3_REGION = os.environ.get('SUPABASE_S3_REGION', 'ap-south-1')
SUPABASE_S3_BUCKET = os.environ.get('SUPABASE_S3_BUCKET', '').strip()
SUPABASE_S3_ACCESS_KEY_ID = (
    os.environ.get('SUPABASE_S3_ACCESS_KEY_ID')
    or os.environ.get('AWS_ACCESS_KEY_ID', '')
).strip()
SUPABASE_S3_SECRET_ACCESS_KEY = (
    os.environ.get('SUPABASE_S3_SECRET_ACCESS_KEY')
    or os.environ.get('AWS_SECRET_ACCESS_KEY', '')
).strip()
SUPABASE_S3_PANORAMA_PREFIX = os.environ.get('SUPABASE_S3_PANORAMA_PREFIX', 'panoramas').strip('/')
SUPABASE_S3_SIGNED_URL_TTL = max(60, int(os.environ.get('SUPABASE_S3_SIGNED_URL_TTL', '900')))
SUPABASE_S3_UPLOAD_URL_TTL = max(60, int(os.environ.get('SUPABASE_S3_UPLOAD_URL_TTL', '900')))
SUPABASE_S3_PLOT_PREFIX = os.environ.get('SUPABASE_S3_PLOT_PREFIX', 'plot-images').strip('/')
SUPABASE_S3_MARKER_PREFIX = os.environ.get('SUPABASE_S3_MARKER_PREFIX', 'marker-images').strip('/')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
IMAGE_CONTENT_TYPES = {
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'webp': 'image/webp',
}
CONTENT_TYPE_TO_EXT = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('templates', exist_ok=True)
os.makedirs('static', exist_ok=True)

_s3_client = None
_s3_signed_url_cache = {}


def _use_s3_for_panoramas():
    return bool(SUPABASE_S3_BUCKET)


def _panorama_upload_serializer():
    # Token is short-lived and only used to prevent clients from referencing
    # arbitrary bucket objects when creating panorama records.
    return URLSafeTimedSerializer(app.secret_key, salt='panorama-upload')


def _plot_upload_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='plot-image-upload')


def _marker_upload_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='marker-image-upload')


def _get_s3_client():
    global _s3_client
    if not _use_s3_for_panoramas():
        return None
    if not boto3:
        return None
    if not SUPABASE_S3_ENDPOINT or not SUPABASE_S3_REGION:
        return None
    if not SUPABASE_S3_BUCKET:
        return None
    if not SUPABASE_S3_ACCESS_KEY_ID or not SUPABASE_S3_SECRET_ACCESS_KEY:
        return None
    if _s3_client is None:
        kwargs = {
            'service_name': 's3',
            'endpoint_url': SUPABASE_S3_ENDPOINT,
            'region_name': SUPABASE_S3_REGION,
            'aws_access_key_id': SUPABASE_S3_ACCESS_KEY_ID,
            'aws_secret_access_key': SUPABASE_S3_SECRET_ACCESS_KEY,
        }
        if BotoConfig:
            kwargs['config'] = BotoConfig(
                signature_version='s3v4',
                s3={'addressing_style': 'path'},
                connect_timeout=5,
                read_timeout=30,
                retries={'max_attempts': 6, 'mode': 'standard'},
            )
        _s3_client = boto3.client(**kwargs)
    return _s3_client


def _panorama_object_key(filename):
    safe_name = os.path.basename(filename or '').strip()
    if SUPABASE_S3_PANORAMA_PREFIX:
        return f"{SUPABASE_S3_PANORAMA_PREFIX}/{safe_name}"
    return safe_name


def _plot_object_key(filename):
    safe_name = os.path.basename(filename or '').strip()
    if SUPABASE_S3_PLOT_PREFIX:
        return f"{SUPABASE_S3_PLOT_PREFIX}/{safe_name}"
    return safe_name


def _marker_object_key(filename):
    safe_name = os.path.basename(filename or '').strip()
    if SUPABASE_S3_MARKER_PREFIX:
        return f"{SUPABASE_S3_MARKER_PREFIX}/{safe_name}"
    return safe_name


def _upload_panorama_to_s3(filename, raw_bytes, content_type):
    client = _get_s3_client()
    if not client:
        missing = []
        if not boto3:
            missing.append('boto3 (pip install -r requirements.txt)')
        if not SUPABASE_S3_ENDPOINT:
            missing.append('SUPABASE_S3_ENDPOINT')
        if not SUPABASE_S3_REGION:
            missing.append('SUPABASE_S3_REGION')
        if not SUPABASE_S3_BUCKET:
            missing.append('SUPABASE_S3_BUCKET')
        if not SUPABASE_S3_ACCESS_KEY_ID:
            missing.append('SUPABASE_S3_ACCESS_KEY_ID')
        if not SUPABASE_S3_SECRET_ACCESS_KEY:
            missing.append('SUPABASE_S3_SECRET_ACCESS_KEY')
        hint = f" Missing: {', '.join(missing)}" if missing else ''
        raise RuntimeError(f'Supabase S3 is not fully configured.{hint}')
    key = _panorama_object_key(filename)
    if hasattr(raw_bytes, 'read'):
        # Use streaming upload when given a file-like object.
        try:
            raw_bytes.seek(0)
        except Exception:
            pass
        if hasattr(client, 'upload_fileobj'):
            client.upload_fileobj(
                raw_bytes,
                SUPABASE_S3_BUCKET,
                key,
                ExtraArgs={'ContentType': content_type},
            )
            return
    client.put_object(
        Bucket=SUPABASE_S3_BUCKET,
        Key=key,
        Body=raw_bytes,
        ContentType=content_type,
    )


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    if request.path.startswith('/api/'):
        max_bytes = app.config.get('MAX_CONTENT_LENGTH') or 0
        max_mb = max(1, int(max_bytes / (1024 * 1024))) if max_bytes else 0
        hint = f' (max {max_mb}MB)' if max_mb else ''
        return jsonify({'error': f'Upload too large{hint}'}), 413
    return _error


def _get_panorama_s3_url(filename):
    client = _get_s3_client()
    if not client:
        return None
    key = _panorama_object_key(filename)
    now = time.time()
    cached = _s3_signed_url_cache.get(key)
    if cached:
        url, expires_at = cached
        # Leave a small buffer so we don't hand out near-expired URLs.
        if url and expires_at and now < (expires_at - 30):
            return url
    try:
        client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    url = client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=SUPABASE_S3_SIGNED_URL_TTL,
    )
    try:
        _s3_signed_url_cache[key] = (url, now + float(SUPABASE_S3_SIGNED_URL_TTL))
    except Exception:
        pass
    return url


def _delete_panorama_from_s3(filename):
    client = _get_s3_client()
    if not client:
        return
    try:
        client.delete_object(Bucket=SUPABASE_S3_BUCKET, Key=_panorama_object_key(filename))
    except Exception:
        pass


def _get_plot_s3_url(filename):
    client = _get_s3_client()
    if not client or not filename:
        return None
    key = _plot_object_key(filename)
    try:
        client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    return client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=SUPABASE_S3_SIGNED_URL_TTL,
    )


def _get_marker_s3_url(filename):
    client = _get_s3_client()
    if not client or not filename:
        return None
    key = _marker_object_key(filename)
    try:
        client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    return client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=SUPABASE_S3_SIGNED_URL_TTL,
    )


def _delete_plot_from_s3(filename):
    client = _get_s3_client()
    if not client or not filename:
        return
    try:
        client.delete_object(Bucket=SUPABASE_S3_BUCKET, Key=_plot_object_key(filename))
    except Exception:
        pass


def _delete_marker_from_s3(filename):
    client = _get_s3_client()
    if not client or not filename:
        return
    try:
        client.delete_object(Bucket=SUPABASE_S3_BUCKET, Key=_marker_object_key(filename))
    except Exception:
        pass


def auth_ctx():
    return {'supabase_url': SUPABASE_URL, 'supabase_anon_key': SUPABASE_ANON_KEY}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _probe_image_dimensions(stream):
    """Return (width, height) without decoding/transcoding the full image.

    Important: avoid ImageOps.exif_transpose() here, because transposing can
    force full decode of very large images and blow dyno memory (Heroku R14).
    """
    try:
        stream.seek(0)
    except Exception:
        pass

    with Image.open(stream) as img:
        width, height = img.size
        # Adjust for EXIF orientation without rotating pixel data.
        try:
            exif = img.getexif()
            orientation = exif.get(EXIF_ORIENTATION_TAG)
            if orientation in (5, 6, 7, 8):
                width, height = height, width
        except Exception:
            pass

    return int(width or 0), int(height or 0)


def _normalize_ext(value):
    ext = (value or '').lower().strip().lstrip('.')
    return ext if ext in ALLOWED_EXTENSIONS else 'jpg'


def _detect_ext_from_content_type(content_type):
    return _normalize_ext(CONTENT_TYPE_TO_EXT.get((content_type or '').lower(), 'jpg'))


# ----- Public routes -----
@app.route('/')
def index():
    return redirect('/login')


@app.route('/login')
def login_page():
    return render_template('login.html', **auth_ctx())


@app.route('/auth/callback')
def auth_callback():
    return render_template('auth_callback.html', **auth_ctx())


# ----- Protected page routes (no API auth; client-side guard) -----
@app.route('/dashboard')
@app.route('/dashboard/<user_id>')
def dashboard(user_id=None):
    return render_template('dashboard.html', user_id=user_id, **auth_ctx())


@app.route('/users')
def users_page():
    return render_template('add_user.html', **auth_ctx())


@app.route('/panorama/<int:panorama_id>/access')
def panorama_access_page(panorama_id):
    """Manage who has client/viewer access to this panorama (admin or owner)."""
    return render_template('panorama_access.html', panorama_id=panorama_id, **auth_ctx())


@app.route('/admin/<int:panorama_id>')
def admin(panorama_id):
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    return render_template('editor.html', panorama=panorama, mode='admin', **auth_ctx())


@app.route('/customer/<int:panorama_id>')
def customer(panorama_id):
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    return render_template('viewer.html', panorama=panorama, **auth_ctx())


@app.route('/client/<int:panorama_id>')
def client(panorama_id):
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    return render_template('client.html', panorama=panorama, mode='client', **auth_ctx())


@app.route('/admin/3d/<int:panorama_id>')
def admin_3d(panorama_id):
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    return render_template('admin_3d.html', panorama=panorama, **auth_ctx())


@app.route('/client/3d/<int:panorama_id>')
def client_3d(panorama_id):
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    return render_template('client_3d.html', panorama=panorama, **auth_ctx())


@app.route('/customer/3d/<int:panorama_id>')
def customer_3d(panorama_id):
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    return render_template('customer_3d.html', panorama=panorama, **auth_ctx())


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve panorama image from Supabase S3 when configured, else legacy sources."""
    s3_url = _get_panorama_s3_url(filename)
    if s3_url:
        resp = redirect(s3_url, code=302)
        # Cache the redirect privately so repeated loads/prefetches reuse the same presigned URL
        # (helps navigation performance without making the signed URL publicly cacheable).
        try:
            resp.headers['Cache-Control'] = f'private, max-age={int(SUPABASE_S3_SIGNED_URL_TTL)}'
        except Exception:
            resp.headers['Cache-Control'] = 'private, max-age=900'
        return resp
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ----- API: require Authorization Bearer token -----

@app.route('/api/panoramas', methods=['GET'])
@require_auth
def get_panoramas(user_id, role):
    """List panoramas for current user (owned + shared). Each has access_type and plot_count."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    items = list_panoramas_for_user(sb, user_id)
    # Serialize for JSON (dates, uuid); drop image blobs
    out = []
    for p in items:
        o = dict(p)
        o.pop('image_data', None)
        o.pop('image_content_type', None)
        for k in ('created_at', 'updated_at'):
            if k in o and o[k]:
                o[k] = str(o[k])
        if 'user_id' in o:
            o['user_id'] = str(o['user_id'])
        out.append(o)
    return jsonify(out)


@app.route('/api/panoramas/upload-url', methods=['POST'])
@require_auth
def create_panorama_upload_url(user_id, role):
    """Return a pre-signed PUT URL for direct-to-S3 panorama uploads (bypasses dyno RAM/timeouts)."""
    if not _use_s3_for_panoramas():
        return jsonify({'error': 'Supabase S3 is not configured'}), 503

    payload = request.get_json(silent=True) or request.form or {}
    original_filename = str(payload.get('original_filename') or payload.get('filename') or '').strip()
    if not original_filename:
        return jsonify({'error': 'original_filename is required'}), 400

    max_bytes = app.config.get('MAX_CONTENT_LENGTH') or 0
    try:
        size_bytes = int(payload.get('size_bytes') or payload.get('size') or 0)
    except Exception:
        size_bytes = 0
    if max_bytes and size_bytes and size_bytes > max_bytes:
        max_mb = max(1, int(max_bytes / (1024 * 1024)))
        return jsonify({'error': f'Upload too large (max {max_mb}MB)'}), 413

    name = str(payload.get('name') or 'panorama').strip()
    if len(name) > 120:
        name = name[:120]

    # Determine extension from filename first; fall back to content-type hint.
    content_type_hint = str(payload.get('content_type') or '').lower().strip()
    ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
    if ext not in ALLOWED_EXTENSIONS and content_type_hint:
        ext = _detect_ext_from_content_type(content_type_hint)
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': 'File type not allowed'}), 400

    safe_project = secure_filename(name) or 'panorama'
    unique = uuid.uuid4().hex[:10]
    filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{safe_project}_{unique}.{ext}"
    image_content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')

    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not fully configured'}), 503

    key = _panorama_object_key(filename)
    try:
        upload_url = client.generate_presigned_url(
            ClientMethod='put_object',
            Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': key, 'ContentType': image_content_type},
            ExpiresIn=int(SUPABASE_S3_UPLOAD_URL_TTL),
        )
    except Exception as e:
        app.logger.exception('Failed to generate panorama upload URL')
        return jsonify({'error': str(e)}), 500

    token = _panorama_upload_serializer().dumps({
        'user_id': str(user_id),
        'filename': filename,
        'content_type': image_content_type,
    })
    return jsonify({
        'filename': filename,
        'upload_url': upload_url,
        'upload_token': token,
        'content_type': image_content_type,
        'max_bytes': max_bytes,
        'expires_in': int(SUPABASE_S3_UPLOAD_URL_TTL),
    })


@app.route('/api/panoramas', methods=['POST'])
@require_auth
def create_panorama(user_id, role):
    def _coerce_dim(value):
        try:
            n = int(str(value).strip())
            if n < 0:
                return 0
            # Hard clamp to keep DB sane if a client sends garbage.
            return min(n, 200000)
        except Exception:
            return 0

    payload = request.form or {}
    name = payload.get('name', 'Untitled Panorama')
    is_360 = str(payload.get('is_360', 'false')).lower() == 'true'
    width = _coerce_dim(payload.get('width'))
    height = _coerce_dim(payload.get('height'))

    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    stored_ok = False
    local_filepath = None
    file = request.files.get('file')

    # ----- Path A: legacy upload through dyno -----
    if file:
        if _use_s3_for_panoramas():
            return jsonify({'error': 'Direct upload required. Use /api/panoramas/upload-url.'}), 400
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed'}), 400

        original_filename = secure_filename(file.filename) or 'upload'
        ext = original_filename.rsplit('.', 1)[1].lower()
        safe_project = secure_filename(name) or 'panorama'
        unique = uuid.uuid4().hex[:10]
        filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{safe_project}_{unique}.{ext}"
        image_content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')

        try:
            if _use_s3_for_panoramas():
                _upload_panorama_to_s3(filename, file.stream, image_content_type)
                stored_ok = True
            else:
                local_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(local_filepath)
                stored_ok = True
        except Exception as e:
            app.logger.exception('Panorama file storage failed')
            return jsonify({'error': str(e)}), 500

    # ----- Path B: direct-to-S3 upload finalize (no dyno file upload) -----
    else:
        filename = os.path.basename(str(payload.get('filename') or '')).strip()
        upload_token = str(payload.get('upload_token') or '').strip()
        if not filename:
            return jsonify({'error': 'filename is required'}), 400
        if not upload_token:
            return jsonify({'error': 'upload_token is required'}), 400
        if not allowed_file(filename):
            return jsonify({'error': 'File type not allowed'}), 400
        if not _use_s3_for_panoramas():
            return jsonify({'error': 'Supabase S3 is not configured'}), 503

        # Verify token (ties the object key to this user and prevents arbitrary key references).
        try:
            signed = _panorama_upload_serializer().loads(
                upload_token,
                max_age=int(SUPABASE_S3_UPLOAD_URL_TTL),
            )
        except SignatureExpired:
            return jsonify({'error': 'upload_token expired; request a new upload URL'}), 400
        except BadSignature:
            return jsonify({'error': 'Invalid upload_token'}), 400

        if str(signed.get('user_id')) != str(user_id) or str(signed.get('filename')) != filename:
            return jsonify({'error': 'Invalid upload_token'}), 403

        ext = filename.rsplit('.', 1)[1].lower()
        image_content_type = str(signed.get('content_type') or IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg'))

        client = _get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not fully configured'}), 503

        # Ensure the object exists and enforce server-side size limit.
        try:
            head = client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=_panorama_object_key(filename))
            stored_ok = True
        except Exception:
            return jsonify({'error': 'Upload not found. Re-upload and try again.'}), 400

        try:
            size_bytes = int(head.get('ContentLength') or 0)
        except Exception:
            size_bytes = 0

        max_bytes = app.config.get('MAX_CONTENT_LENGTH') or 0
        if max_bytes and size_bytes and size_bytes > max_bytes:
            _delete_panorama_from_s3(filename)
            max_mb = max(1, int(max_bytes / (1024 * 1024)))
            return jsonify({'error': f'Upload too large (max {max_mb}MB)'}), 413

        original_filename = secure_filename(str(payload.get('original_filename') or filename)) or 'upload'

    # ----- DB insert (shared) -----
    try:
        insert_row = {
            'user_id': user_id,
            'name': name,
            'filename': filename,
            'original_filename': original_filename,
            'width': width,
            'height': height,
            'is_360': is_360,
            'image_content_type': image_content_type,
        }
        r = sb.table('panoramas').insert(insert_row).execute()
        if not r.data or len(r.data) == 0:
            raise RuntimeError('Insert failed')
        panorama_id = r.data[0]['id']

        return jsonify({
            'id': panorama_id,
            'name': name,
            'filename': filename,
            'width': width,
            'height': height,
            'is_360': is_360,
            'access_type': 'owner',
        }), 201
    except Exception as e:
        app.logger.exception('Panorama upload failed')
        if stored_ok:
            if _use_s3_for_panoramas():
                _delete_panorama_from_s3(filename)
            elif local_filepath and os.path.exists(local_filepath):
                try:
                    os.remove(local_filepath)
                except Exception:
                    pass
        return jsonify({'error': str(e)}), 500


@app.route('/api/panoramas/<int:panorama_id>', methods=['DELETE'])
@require_auth
def delete_panorama(user_id, role, panorama_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    if not can_delete_panorama(access_type):
        return jsonify({'error': 'Only the owner can delete this panorama'}), 403

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], panorama['filename'])
    _delete_panorama_from_s3(panorama['filename'])
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            pass
    try:
        sb.table('panoramas').delete().eq('id', panorama_id).execute()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/panoramas/<int:panorama_id>', methods=['PUT', 'PATCH'])
@require_auth
def rename_panorama(user_id, role, panorama_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    if not can_delete_panorama(access_type):
        return jsonify({'error': 'Only the owner can rename this panorama'}), 403

    payload = request.get_json(silent=True) or request.form or {}
    name = str(payload.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'Project name is required'}), 400
    if len(name) > 120:
        return jsonify({'error': 'Project name must be 120 characters or fewer'}), 400

    try:
        # supabase-py v2 postgrest update builders don't support chaining .select() after .update().
        # Do the update, then (if needed) fetch the row.
        response = sb.table('panoramas').update({
            'name': name,
            'updated_at': datetime.utcnow().isoformat(),
        }).eq('id', panorama_id).execute()
        err = getattr(response, 'error', None)
        if err:
            message = getattr(err, 'message', None) or str(err)
            return jsonify({'error': message}), 500

        row = None
        data = getattr(response, 'data', None)
        if isinstance(data, list) and data:
            row = data[0]

        if not row:
            # Fallback: explicit fetch for clients/configs that don't return updated rows.
            fetch = sb.table('panoramas').select('id, name, updated_at').eq('id', panorama_id).limit(1).execute()
            ferr = getattr(fetch, 'error', None)
            if ferr:
                message = getattr(ferr, 'message', None) or str(ferr)
                return jsonify({'error': message}), 500
            fdata = getattr(fetch, 'data', None)
            if isinstance(fdata, list) and fdata:
                row = fdata[0]

        if row:
            return jsonify({
                'id': row.get('id', panorama_id),
                'name': row.get('name', name),
                'updated_at': str(row.get('updated_at') or datetime.utcnow().isoformat()),
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({
        'id': panorama_id,
        'name': name,
        'updated_at': datetime.utcnow().isoformat(),
    })


@app.route('/api/panoramas/<int:panorama_id>/plots', methods=['GET'])
@require_auth
def get_plots(user_id, role, panorama_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    try:
        # Keep list payload light: do not read image_data blob here.
        fields_base = (
            'id, panorama_id, name, area, price, status, description, color, '
            'media_photo, media_video, points, created_at, updated_at, image_filename'
        )
        fields_with_links = (
            'id, panorama_id, name, area, price, status, description, color, '
            'media_photo, media_video, linked_panorama_id, points, created_at, updated_at, image_filename'
        )
        try:
            r = sb.table('plots').select(fields_with_links).eq('panorama_id', panorama_id).order('created_at').execute()
        except Exception as e:
            # Backward-compatible: linked_panorama_id column may not exist yet.
            if 'linked_panorama_id' in str(e).lower():
                r = sb.table('plots').select(fields_base).eq('panorama_id', panorama_id).order('created_at').execute()
            else:
                raise
        plots = []
        for row in (r.data or []):
            p = dict(row)
            if isinstance(p.get('points'), str):
                try:
                    p['points'] = json.loads(p['points'])
                except Exception:
                    p['points'] = []
            # Frontend loads image by id; use content type presence as a lightweight has_image flag.
            p['has_image'] = bool(p.get('image_filename'))
            p.pop('image_filename', None)
            plots.append(p)
        return jsonify(plots)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/panoramas/<int:panorama_id>/plots', methods=['POST'])
@require_auth
def create_plot(user_id, role, panorama_id):
    data = request.get_json()
    if not data or not data.get('name') or not data.get('points'):
        return jsonify({'error': 'Name and points are required'}), 400
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    if not can_edit_plots(access_type):
        return jsonify({'error': 'You cannot add plots'}), 403
    try:
        insert_row = {
            'panorama_id': panorama_id,
            'name': data['name'],
            'area': data.get('area', ''),
            'price': data.get('price', ''),
            'status': data.get('status', 'available'),
            'description': data.get('description', ''),
            'color': data.get('color', 'emerald'),
            'media_photo': data.get('media_photo', ''),
            'media_video': data.get('media_video', ''),
            'points': data['points'],
        }
        if 'linked_panorama_id' in (data or {}):
            raw_link = data.get('linked_panorama_id')
            link_id = None
            if raw_link is not None and str(raw_link).strip() != '':
                try:
                    link_id = int(raw_link)
                except Exception:
                    link_id = None
            if link_id is not None:
                insert_row['linked_panorama_id'] = link_id
        try:
            r = sb.table('plots').insert(insert_row).execute()
        except Exception as e:
            # Backward-compatible: linked_panorama_id column may not exist yet.
            if 'linked_panorama_id' in str(e).lower():
                insert_row.pop('linked_panorama_id', None)
                r = sb.table('plots').insert(insert_row).execute()
            else:
                raise
        if not r.data or len(r.data) == 0:
            return jsonify({'error': 'Insert failed'}), 500
        row = r.data[0]
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
        return jsonify({'id': row['id'], **data}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/plots/<int:plot_id>', methods=['DELETE'])
@require_auth
def delete_plot(user_id, role, plot_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    image_filename = ''
    try:
        pl = sb.table('plots').select('panorama_id, image_filename').eq('id', plot_id).limit(1).execute()
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        panorama_id = pl.data[0]['panorama_id']
        image_filename = pl.data[0].get('image_filename') or ''
    except Exception:
        return jsonify({'error': 'Not found'}), 404
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama or not can_edit_plots(access_type):
        return jsonify({'error': 'Forbidden'}), 403
    try:
        sb.table('plots').delete().eq('id', plot_id).execute()
        if image_filename:
            _delete_plot_from_s3(image_filename)
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/plots/<int:plot_id>', methods=['PUT'])
@require_auth
def update_plot(user_id, role, plot_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Body required'}), 400
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        pl = sb.table('plots').select('panorama_id').eq('id', plot_id).limit(1).execute()
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        panorama_id = pl.data[0]['panorama_id']
    except Exception:
        return jsonify({'error': 'Not found'}), 404
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama or not can_edit_plots(access_type):
        return jsonify({'error': 'Forbidden'}), 403
    try:
        upd = {
            'name': data.get('name'),
            'area': data.get('area', ''),
            'price': data.get('price', ''),
            'status': data.get('status', 'available'),
            'description': data.get('description', ''),
            'color': data.get('color', 'emerald'),
            'media_photo': data.get('media_photo', ''),
            'media_video': data.get('media_video', ''),
            'points': data.get('points', []),
            'updated_at': datetime.utcnow().isoformat(),
        }
        if 'linked_panorama_id' in data:
            raw_link = data.get('linked_panorama_id')
            link_id = None
            if raw_link is not None and str(raw_link).strip() != '':
                try:
                    link_id = int(raw_link)
                except Exception:
                    link_id = None
            # Explicit null clears the link.
            upd['linked_panorama_id'] = link_id
        try:
            sb.table('plots').update(upd).eq('id', plot_id).execute()
        except Exception as e:
            # Backward-compatible: linked_panorama_id column may not exist yet.
            if 'linked_panorama_id' in str(e).lower():
                upd.pop('linked_panorama_id', None)
                sb.table('plots').update(upd).eq('id', plot_id).execute()
            else:
                raise
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


# ----- Plot image (stored in S3; DB stores filename) -----
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_PLOT_IMAGE_BYTES = 12 * 1024 * 1024  # 12MB raw upload limit before compression
MAX_MARKER_IMAGE_BYTES = int(os.environ.get('MAX_MARKER_IMAGE_BYTES', str(3 * 1024 * 1024)))


@app.route('/api/plots/<int:plot_id>/image', methods=['GET'])
@require_auth
def get_plot_image(user_id, role, plot_id):
    """Serve plot image from S3 (any access to panorama can view)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        pl = sb.table('plots').select('panorama_id, image_filename').eq('id', plot_id).limit(1).execute()
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        row = pl.data[0]
        panorama_id = row['panorama_id']
    except Exception:
        return jsonify({'error': 'Not found'}), 404
    panorama, _ = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Forbidden'}), 403
    image_filename = row.get('image_filename') or ''
    if not image_filename:
        return jsonify({'error': 'No image'}), 404
    s3_url = _get_plot_s3_url(image_filename)
    if not s3_url:
        return jsonify({'error': 'Image not found'}), 404
    resp = redirect(s3_url, code=302)
    resp.headers['Cache-Control'] = 'private, max-age=900'
    return resp


@app.route('/api/plots/<int:plot_id>/image/upload-url', methods=['POST'])
@require_auth
def create_plot_image_upload_url(user_id, role, plot_id):
    """Return a pre-signed PUT URL for direct-to-S3 plot image uploads."""
    if not _use_s3_for_panoramas():
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        pl = sb.table('plots').select('panorama_id').eq('id', plot_id).limit(1).execute()
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        panorama_id = pl.data[0]['panorama_id']
    except Exception:
        return jsonify({'error': 'Not found'}), 404
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama or not can_edit_plots(access_type):
        return jsonify({'error': 'Forbidden'}), 403

    payload = request.get_json(silent=True) or request.form or {}
    original_filename = str(payload.get('original_filename') or payload.get('filename') or '').strip()
    if not original_filename:
        return jsonify({'error': 'original_filename is required'}), 400

    try:
        size_bytes = int(payload.get('size_bytes') or payload.get('size') or 0)
    except Exception:
        size_bytes = 0
    if size_bytes and size_bytes > MAX_PLOT_IMAGE_BYTES:
        return jsonify({'error': f'Image too large (max {MAX_PLOT_IMAGE_BYTES // (1024*1024)}MB)'}), 413

    content_type_hint = str(payload.get('content_type') or '').lower().strip()
    ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
    if ext not in ALLOWED_IMAGE_EXTENSIONS and content_type_hint:
        ext = _detect_ext_from_content_type(content_type_hint)
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({'error': 'File type not allowed. Use: png, jpg, jpeg, gif, webp'}), 400

    unique = uuid.uuid4().hex[:10]
    filename = f"plot_{plot_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{unique}.{ext}"
    content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')

    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not fully configured'}), 503

    try:
        upload_url = client.generate_presigned_url(
            ClientMethod='put_object',
            Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': _plot_object_key(filename), 'ContentType': content_type},
            ExpiresIn=int(SUPABASE_S3_UPLOAD_URL_TTL),
        )
    except Exception as e:
        app.logger.exception('Failed to generate plot image upload URL')
        return jsonify({'error': str(e)}), 500

    token = _plot_upload_serializer().dumps({
        'user_id': str(user_id),
        'plot_id': int(plot_id),
        'filename': filename,
        'content_type': content_type,
    })
    return jsonify({
        'filename': filename,
        'upload_url': upload_url,
        'upload_token': token,
        'content_type': content_type,
        'max_bytes': MAX_PLOT_IMAGE_BYTES,
        'expires_in': int(SUPABASE_S3_UPLOAD_URL_TTL),
    })


@app.route('/api/plots/<int:plot_id>/image', methods=['POST', 'PUT'])
@require_auth
def upload_plot_image(user_id, role, plot_id):
    """Finalize plot image upload (S3 only)."""
    if request.files.get('file'):
        return jsonify({'error': 'Direct upload required. Use /api/plots/<id>/image/upload-url.'}), 400
    if not _use_s3_for_panoramas():
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    payload = request.form or {}
    filename = os.path.basename(str(payload.get('filename') or '')).strip()
    upload_token = str(payload.get('upload_token') or '').strip()
    if not filename:
        return jsonify({'error': 'filename is required'}), 400
    if not upload_token:
        return jsonify({'error': 'upload_token is required'}), 400

    try:
        pl = sb.table('plots').select('panorama_id').eq('id', plot_id).limit(1).execute()
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        panorama_id = pl.data[0]['panorama_id']
    except Exception:
        return jsonify({'error': 'Not found'}), 404
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama or not can_edit_plots(access_type):
        return jsonify({'error': 'Forbidden'}), 403

    try:
        signed = _plot_upload_serializer().loads(upload_token, max_age=int(SUPABASE_S3_UPLOAD_URL_TTL))
    except SignatureExpired:
        return jsonify({'error': 'upload_token expired; request a new upload URL'}), 400
    except BadSignature:
        return jsonify({'error': 'Invalid upload_token'}), 400

    if str(signed.get('user_id')) != str(user_id) or int(signed.get('plot_id')) != int(plot_id) or str(signed.get('filename')) != filename:
        return jsonify({'error': 'Invalid upload_token'}), 403

    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not fully configured'}), 503

    try:
        head = client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=_plot_object_key(filename))
    except Exception:
        return jsonify({'error': 'Upload not found. Re-upload and try again.'}), 400

    try:
        size_bytes = int(head.get('ContentLength') or 0)
    except Exception:
        size_bytes = 0
    if size_bytes and size_bytes > MAX_PLOT_IMAGE_BYTES:
        _delete_plot_from_s3(filename)
        return jsonify({'error': f'Image too large (max {MAX_PLOT_IMAGE_BYTES // (1024*1024)}MB)'}), 413

    try:
        sb.table('plots').update({
            'image_filename': filename,
            'image_content_type': str(signed.get('content_type') or ''),
        }).eq('id', plot_id).execute()
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'success': True})


@app.route('/api/markers/<marker_id>/image', methods=['GET'])
@require_auth
def get_marker_image(user_id, role, marker_id):
    """Fetch a single marker image from S3."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    try:
        r = sb.table('plot_markers').select('id, plot_id, image_filename').eq('id', marker_id).limit(1).execute()
        if not r.data:
            return jsonify({'error': 'Marker not found'}), 404
        row = r.data[0]
    except Exception:
        return jsonify({'error': 'Marker not found'}), 404

    plot_id = str(row.get('plot_id') or '').strip()
    if not plot_id:
        return jsonify({'error': 'Invalid marker'}), 400

    try:
        panorama_id = int(plot_id)
    except Exception:
        return jsonify({'error': 'Invalid marker reference'}), 400

    panorama, _ = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Forbidden'}), 403

    image_filename = row.get('image_filename') or ''
    if not image_filename:
        return jsonify({'error': 'No image'}), 404

    s3_url = _get_marker_s3_url(image_filename)
    if not s3_url:
        return jsonify({'error': 'Image not found'}), 404
    resp = redirect(s3_url, code=302)
    resp.headers['Cache-Control'] = 'private, max-age=900'
    return resp


@app.route('/api/markers/<marker_id>/image/upload-url', methods=['POST'])
@require_auth
def create_marker_image_upload_url(user_id, role, marker_id):
    """Return a pre-signed PUT URL for direct-to-S3 marker image uploads."""
    if not _use_s3_for_panoramas():
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    try:
        r = sb.table('plot_markers').select('id, plot_id').eq('id', marker_id).limit(1).execute()
        if not r.data:
            return jsonify({'error': 'Marker not found'}), 404
        row = r.data[0]
    except Exception:
        return jsonify({'error': 'Marker not found'}), 404

    plot_id = str(row.get('plot_id') or '').strip()
    if not plot_id:
        return jsonify({'error': 'Invalid marker'}), 400
    try:
        panorama_id = int(plot_id)
    except Exception:
        return jsonify({'error': 'Invalid marker reference'}), 400
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama or not can_edit_plots(access_type):
        return jsonify({'error': 'Forbidden'}), 403

    payload = request.get_json(silent=True) or request.form or {}
    original_filename = str(payload.get('original_filename') or payload.get('filename') or '').strip()
    if not original_filename:
        return jsonify({'error': 'original_filename is required'}), 400
    try:
        size_bytes = int(payload.get('size_bytes') or payload.get('size') or 0)
    except Exception:
        size_bytes = 0
    if size_bytes and size_bytes > MAX_MARKER_IMAGE_BYTES:
        return jsonify({'error': f'Image too large (max {MAX_MARKER_IMAGE_BYTES // (1024*1024)}MB)'}), 413

    content_type_hint = str(payload.get('content_type') or '').lower().strip()
    ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
    if ext not in ALLOWED_IMAGE_EXTENSIONS and content_type_hint:
        ext = _detect_ext_from_content_type(content_type_hint)
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({'error': 'File type not allowed. Use: png, jpg, jpeg, gif, webp'}), 400

    unique = uuid.uuid4().hex[:10]
    filename = f"marker_{marker_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{unique}.{ext}"
    content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')

    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not fully configured'}), 503

    try:
        upload_url = client.generate_presigned_url(
            ClientMethod='put_object',
            Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': _marker_object_key(filename), 'ContentType': content_type},
            ExpiresIn=int(SUPABASE_S3_UPLOAD_URL_TTL),
        )
    except Exception as e:
        app.logger.exception('Failed to generate marker image upload URL')
        return jsonify({'error': str(e)}), 500

    token = _marker_upload_serializer().dumps({
        'user_id': str(user_id),
        'marker_id': str(marker_id),
        'filename': filename,
        'content_type': content_type,
    })
    return jsonify({
        'filename': filename,
        'upload_url': upload_url,
        'upload_token': token,
        'content_type': content_type,
        'max_bytes': MAX_MARKER_IMAGE_BYTES,
        'expires_in': int(SUPABASE_S3_UPLOAD_URL_TTL),
    })


@app.route('/api/markers/<marker_id>/image', methods=['POST', 'PUT'])
@require_auth
def upload_marker_image(user_id, role, marker_id):
    """Finalize marker image upload (S3 only)."""
    if request.files.get('file'):
        return jsonify({'error': 'Direct upload required. Use /api/markers/<id>/image/upload-url.'}), 400
    if not _use_s3_for_panoramas():
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    payload = request.form or {}
    filename = os.path.basename(str(payload.get('filename') or '')).strip()
    upload_token = str(payload.get('upload_token') or '').strip()
    if not filename:
        return jsonify({'error': 'filename is required'}), 400
    if not upload_token:
        return jsonify({'error': 'upload_token is required'}), 400

    try:
        r = sb.table('plot_markers').select('id, plot_id').eq('id', marker_id).limit(1).execute()
        if not r.data:
            return jsonify({'error': 'Marker not found'}), 404
        row = r.data[0]
    except Exception:
        return jsonify({'error': 'Marker not found'}), 404

    plot_id = str(row.get('plot_id') or '').strip()
    if not plot_id:
        return jsonify({'error': 'Invalid marker'}), 400
    try:
        panorama_id = int(plot_id)
    except Exception:
        return jsonify({'error': 'Invalid marker reference'}), 400
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama or not can_edit_plots(access_type):
        return jsonify({'error': 'Forbidden'}), 403

    try:
        signed = _marker_upload_serializer().loads(upload_token, max_age=int(SUPABASE_S3_UPLOAD_URL_TTL))
    except SignatureExpired:
        return jsonify({'error': 'upload_token expired; request a new upload URL'}), 400
    except BadSignature:
        return jsonify({'error': 'Invalid upload_token'}), 400

    if str(signed.get('user_id')) != str(user_id) or str(signed.get('marker_id')) != str(marker_id) or str(signed.get('filename')) != filename:
        return jsonify({'error': 'Invalid upload_token'}), 403

    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not fully configured'}), 503
    try:
        head = client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=_marker_object_key(filename))
    except Exception:
        return jsonify({'error': 'Upload not found. Re-upload and try again.'}), 400
    try:
        size_bytes = int(head.get('ContentLength') or 0)
    except Exception:
        size_bytes = 0
    if size_bytes and size_bytes > MAX_MARKER_IMAGE_BYTES:
        _delete_marker_from_s3(filename)
        return jsonify({'error': f'Image too large (max {MAX_MARKER_IMAGE_BYTES // (1024*1024)}MB)'}), 413

    try:
        sb.table('plot_markers').update({
            'image_filename': filename,
        }).eq('id', marker_id).execute()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'success': True})


# ----- Admin: create user + set as user in profiles -----
@app.route('/api/admin/users', methods=['POST'])
@require_auth
def admin_create_user(user_id, role):
    if role != 'admin':
        return jsonify({'error': 'Only admins can create users'}), 403
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return jsonify({'error': 'Server not configured for creating users'}), 503
    data = request.get_json() or {}
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''
    display_name = (data.get('display_name') or '').strip() or None
    as_admin = data.get('role') == 'admin'
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        resp = sb.auth.admin.create_user({
            'email': email,
            'password': password,
            'email_confirm': True,
        })
        uid = resp.user.id
        sb.table('profiles').upsert({
            'user_id': uid,
            'role': 'admin' if as_admin else 'user',
            'email': getattr(resp.user, 'email', None) or email,
            'display_name': display_name,
        }, on_conflict='user_id').execute()
        return jsonify({'success': True, 'user': {'id': str(uid), 'email': resp.user.email, 'display_name': display_name}}), 201
    except Exception as e:
        err = str(e)
        if 'already registered' in err.lower() or 'already exists' in err.lower():
            return jsonify({'error': 'A user with this email already exists'}), 409
        return jsonify({'error': err or 'Failed to create user'}), 400


# ----- Current user profile (display_name, email) -----
@app.route('/api/profile/me', methods=['PUT', 'PATCH'])
@require_auth
def update_my_profile(user_id, role):
    """Update current user's profile display_name and/or email. Uses upsert so name is stored even if trigger hasn't created the row yet."""
    data = request.get_json() or {}
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    if 'display_name' not in data and 'email' not in data:
        return jsonify({'error': 'Provide display_name and/or email'}), 400
    display_name = (data.get('display_name') or '').strip() or None if 'display_name' in data else None
    email = (data.get('email') or '').strip() or None if 'email' in data else None
    updated_at = datetime.utcnow().isoformat()
    existing = get_profile(sb, user_id)
    payload = {
        'user_id': user_id,
        'role': (existing.get('role') if existing else 'user'),
        'updated_at': updated_at,
    }
    if 'display_name' in data:
        payload['display_name'] = display_name
    if 'email' in data:
        payload['email'] = email
    try:
        sb.table('profiles').upsert(payload, on_conflict='user_id').execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ----- List users (admin only) for granting access -----
@app.route('/api/profiles', methods=['GET'])
@require_admin
def list_profiles(user_id, role):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        r = sb.table('profiles').select('user_id, role, display_name, email, created_at').order('created_at', desc=True).execute()
        out = []
        for row in (r.data or []):
            o = dict(row)
            o['user_id'] = str(o['user_id'])
            if o.get('created_at'):
                o['created_at'] = str(o['created_at'])
            out.append(o)
        return jsonify(out)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ----- Panorama access: grant / revoke (admin or panorama owner) -----
@app.route('/api/panoramas/<int:panorama_id>/access', methods=['GET'])
@require_auth
def get_panorama_access(user_id, role, panorama_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    if access_type != 'owner' and role != 'admin':
        return jsonify({'error': 'Only owner or admin can list access'}), 403
    try:
        r = sb.table('panorama_access').select('*').eq('panorama_id', panorama_id).execute()
        for row in (r.data or []):
            row['user_id'] = str(row['user_id'])
            if row.get('granted_by'):
                row['granted_by'] = str(row['granted_by'])
        return jsonify(r.data or [])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/panoramas/<int:panorama_id>/access', methods=['POST'])
@require_auth
def grant_panorama_access(user_id, role, panorama_id):
    """Grant client or viewer access to a user. Body: { "user_id": "<uuid>", "access_type": "client"|"viewer" }"""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    if access_type != 'owner' and role != 'admin':
        return jsonify({'error': 'Only owner or admin can grant access'}), 403
    data = request.get_json() or {}
    target_user_id = (data.get('user_id') or '').strip()
    at = (data.get('access_type') or 'viewer').lower()
    if at not in ('client', 'viewer'):
        at = 'viewer'
    if not target_user_id:
        return jsonify({'error': 'user_id is required'}), 400
    if target_user_id == user_id:
        return jsonify({'error': 'Cannot grant access to yourself'}), 400
    try:
        sb.table('panorama_access').upsert({
            'panorama_id': panorama_id,
            'user_id': target_user_id,
            'access_type': at,
            'granted_by': user_id,
        }, on_conflict='panorama_id,user_id').execute()
        return jsonify({'success': True}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/panoramas/<int:panorama_id>/access/<target_user_id>', methods=['DELETE'])
@require_auth
def revoke_panorama_access(user_id, role, panorama_id, target_user_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    if access_type != 'owner' and role != 'admin':
        return jsonify({'error': 'Only owner or admin can revoke access'}), 403
    try:
        sb.table('panorama_access').delete().eq('panorama_id', panorama_id).eq('user_id', target_user_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ----- Set profile role (admin only) -----
@app.route('/api/profiles/<user_id>/role', methods=['PUT'])
@require_admin
def set_profile_role(admin_id, role, user_id):
    data = request.get_json() or {}
    new_role = (data.get('role') or 'user').lower()
    if new_role not in ('admin', 'user'):
        return jsonify({'error': 'role must be admin or user'}), 400
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        sb.table('profiles').update({'role': new_role, 'updated_at': datetime.utcnow().isoformat()}).eq('user_id', user_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug, host='0.0.0.0', port=port)
