"""
Real Estate Panorama Plot Marker - Flask Application
Uses Supabase for Auth + DB. All data is user-scoped; admin can grant client/viewer access.
"""
import os
import json
import base64
import io
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

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

from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, Response
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps
try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:
    boto3 = None
    BotoConfig = None

RESAMPLE_LANCZOS = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS

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
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
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


def _use_s3_for_panoramas():
    return bool(SUPABASE_S3_BUCKET)


def _get_s3_client():
    global _s3_client
    if not _use_s3_for_panoramas():
        return None
    if not boto3:
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
            kwargs['config'] = BotoConfig(signature_version='s3v4', s3={'addressing_style': 'path'})
        _s3_client = boto3.client(**kwargs)
    return _s3_client


def _panorama_object_key(filename):
    safe_name = os.path.basename(filename or '').strip()
    if SUPABASE_S3_PANORAMA_PREFIX:
        return f"{SUPABASE_S3_PANORAMA_PREFIX}/{safe_name}"
    return safe_name


def _upload_panorama_to_s3(filename, raw_bytes, content_type):
    client = _get_s3_client()
    if not client:
        raise RuntimeError('Supabase S3 is not fully configured')
    client.put_object(
        Bucket=SUPABASE_S3_BUCKET,
        Key=_panorama_object_key(filename),
        Body=raw_bytes,
        ContentType=content_type,
    )


def _get_panorama_s3_url(filename):
    client = _get_s3_client()
    if not client:
        return None
    key = _panorama_object_key(filename)
    try:
        client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    return client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=SUPABASE_S3_SIGNED_URL_TTL,
    )


def _delete_panorama_from_s3(filename):
    client = _get_s3_client()
    if not client:
        return
    try:
        client.delete_object(Bucket=SUPABASE_S3_BUCKET, Key=_panorama_object_key(filename))
    except Exception:
        pass


def auth_ctx():
    return {'supabase_url': SUPABASE_URL, 'supabase_anon_key': SUPABASE_ANON_KEY}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _target_plot_image_bytes():
    return int(os.environ.get('PLOT_IMAGE_TARGET_BYTES', str(420 * 1024)))


def _max_plot_image_edge():
    return int(os.environ.get('PLOT_IMAGE_MAX_EDGE', '1600'))


def _normalize_ext(value):
    ext = (value or '').lower().strip().lstrip('.')
    return ext if ext in ALLOWED_EXTENSIONS else 'jpg'


def _detect_ext_from_content_type(content_type):
    return _normalize_ext(CONTENT_TYPE_TO_EXT.get((content_type or '').lower(), 'jpg'))


def _encode_image_bytes(image, fmt, quality):
    buffer = io.BytesIO()
    if fmt == 'JPEG':
        img = image if image.mode == 'RGB' else image.convert('RGB')
        img.save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
        return buffer.getvalue(), 'image/jpeg'
    img = image if image.mode in ('RGB', 'RGBA') else image.convert('RGBA')
    img.save(buffer, format='WEBP', quality=quality, method=6)
    return buffer.getvalue(), 'image/webp'


def compress_plot_image_bytes(raw_bytes, preferred_ext='jpg', target_bytes=None, max_edge=None):
    """Compress plot image for DB storage and return (bytes, content_type)."""
    target_bytes = target_bytes or _target_plot_image_bytes()
    max_edge = max_edge or _max_plot_image_edge()
    preferred_ext = _normalize_ext(preferred_ext)

    with Image.open(io.BytesIO(raw_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        has_alpha = ('A' in img.getbands()) or ('transparency' in img.info)

        if has_alpha:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        max_side = max(img.width, img.height) or 1
        if max_side > max_edge:
            scale = max_edge / float(max_side)
            new_size = (
                max(1, int(round(img.width * scale))),
                max(1, int(round(img.height * scale))),
            )
            img = img.resize(new_size, RESAMPLE_LANCZOS)

        if preferred_ext == 'webp':
            output_format = 'WEBP'
        elif preferred_ext in ('png', 'gif'):
            output_format = 'WEBP'
        else:
            output_format = 'WEBP' if has_alpha else 'JPEG'

        quality = 84
        working = img
        best_data = None
        best_type = 'image/jpeg'

        for attempt in range(10):
            encoded, content_type = _encode_image_bytes(working, output_format, quality)
            if best_data is None or len(encoded) < len(best_data):
                best_data, best_type = encoded, content_type
            if len(encoded) <= target_bytes:
                return encoded, content_type

            quality = max(42, quality - 8)
            if attempt in (3, 6, 8):
                nw = max(320, int(round(working.width * 0.87)))
                nh = max(240, int(round(working.height * 0.87)))
                if nw < working.width and nh < working.height:
                    working = working.resize((nw, nh), RESAMPLE_LANCZOS)

        return best_data, best_type


def maybe_compress_plot_image_base64(image_b64, content_type):
    """Return (b64, content_type, changed) for legacy oversized/unoptimized images."""
    if not image_b64:
        return image_b64, content_type, False
    try:
        raw = base64.b64decode(image_b64)
    except Exception:
        return image_b64, content_type, False

    target_bytes = _target_plot_image_bytes()
    should_recompress = (
        len(raw) > target_bytes
        or (content_type or '').lower() not in ('image/jpeg', 'image/webp')
    )
    if not should_recompress:
        return image_b64, content_type, False

    try:
        preferred_ext = _detect_ext_from_content_type(content_type)
        compressed, new_type = compress_plot_image_bytes(raw, preferred_ext=preferred_ext)
    except Exception:
        return image_b64, content_type, False

    if not compressed:
        return image_b64, content_type, False
    if len(compressed) >= len(raw):
        return image_b64, content_type, False

    return base64.b64encode(compressed).decode('ascii'), new_type, True


def _decode_base64_or_data_url(value):
    if not value:
        return None, None
    raw_value = str(value).strip()
    content_type = None
    payload = raw_value
    if raw_value.startswith('data:') and ',' in raw_value:
        header, payload = raw_value.split(',', 1)
        if ';' in header:
            content_type = header[5:header.find(';')]
        else:
            content_type = header[5:]
    try:
        return base64.b64decode(payload), (content_type or 'image/jpeg')
    except Exception:
        return None, content_type


def _encode_data_url(raw_bytes, content_type):
    return f"data:{content_type};base64,{base64.b64encode(raw_bytes).decode('ascii')}"


def maybe_compress_marker_image_data(image_value):
    """Return (image_value, changed) for marker image_base64 (data URL or plain base64)."""
    raw, content_type = _decode_base64_or_data_url(image_value)
    if not raw:
        return image_value, False

    target_bytes = int(os.environ.get('MARKER_IMAGE_TARGET_BYTES', str(300 * 1024)))
    should_recompress = (
        len(raw) > target_bytes
        or (content_type or '').lower() not in ('image/jpeg', 'image/webp')
    )
    if not should_recompress:
        return image_value, False

    preferred_ext = _detect_ext_from_content_type(content_type)
    try:
        compressed, new_type = compress_plot_image_bytes(
            raw,
            preferred_ext=preferred_ext,
            target_bytes=target_bytes,
            max_edge=int(os.environ.get('MARKER_IMAGE_MAX_EDGE', '1200')),
        )
    except Exception:
        return image_value, False

    if not compressed or len(compressed) >= len(raw):
        return image_value, False
    return _encode_data_url(compressed, new_type), True


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
        return redirect(s3_url, code=302)

    sb = get_supabase()
    if sb:
        try:
            r = sb.table('panoramas').select('image_data, image_content_type').eq('filename', filename).limit(1).execute()
            if r.data and len(r.data) > 0 and r.data[0].get('image_data'):
                row = r.data[0]
                data = base64.b64decode(row['image_data'])
                content_type = row.get('image_content_type') or 'image/jpeg'
                return Response(data, mimetype=content_type)
        except Exception:
            pass
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


@app.route('/api/panoramas', methods=['POST'])
@require_auth
def create_panorama(user_id, role):
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    name = request.form.get('name', 'Untitled Panorama')
    is_360 = request.form.get('is_360', 'false').lower() == 'true'
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400

    original_filename = secure_filename(file.filename)
    ext = original_filename.rsplit('.', 1)[1].lower()
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secure_filename(name)}.{ext}"
    raw = file.read()
    if not raw:
        return jsonify({'error': 'Uploaded file is empty'}), 400

    try:
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size
    except Exception:
        width, height = 0, 0

    image_content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
    local_filepath = None

    if _use_s3_for_panoramas():
        try:
            _upload_panorama_to_s3(filename, raw, image_content_type)
        except Exception as e:
            return jsonify({'error': f'Panorama upload to Supabase S3 failed: {e}'}), 500
    else:
        local_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        try:
            with open(local_filepath, 'wb') as f:
                f.write(raw)
        except Exception as e:
            return jsonify({'error': f'Failed to save panorama file: {e}'}), 500

    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
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
    try:
        r = sb.table('panoramas').insert(insert_row).execute()
        if not r.data or len(r.data) == 0:
            return jsonify({'error': 'Insert failed'}), 500
        row = r.data[0]
        panorama_id = row['id']
    except Exception as e:
        if _use_s3_for_panoramas():
            _delete_panorama_from_s3(filename)
        elif local_filepath and os.path.exists(local_filepath):
            try:
                os.remove(local_filepath)
            except Exception:
                pass
        return jsonify({'error': str(e)}), 500

    return jsonify({
        'id': panorama_id,
        'name': name,
        'filename': filename,
        'width': width,
        'height': height,
        'is_360': is_360,
        'access_type': 'owner',
    }), 201


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
        r = sb.table('plots').select(
            'id, panorama_id, name, area, price, status, description, color, media_photo, media_video, points, created_at, updated_at, image_content_type'
        ).eq('panorama_id', panorama_id).order('created_at').execute()
        legacy_image_ids = set()
        try:
            legacy = sb.table('plots').select('id').eq('panorama_id', panorama_id).not_.is_('image_data', 'null').execute()
            legacy_image_ids = {row.get('id') for row in (legacy.data or []) if row.get('id') is not None}
        except Exception:
            legacy_image_ids = set()
        plots = []
        for row in (r.data or []):
            p = dict(row)
            if isinstance(p.get('points'), str):
                try:
                    p['points'] = json.loads(p['points'])
                except Exception:
                    p['points'] = []
            # Frontend loads image by id; use content type presence as a lightweight has_image flag.
            p['has_image'] = bool(p.get('image_content_type')) or (p.get('id') in legacy_image_ids)
            p.pop('image_content_type', None)
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
        r = sb.table('plots').insert({
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
        }).execute()
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
        sb.table('plots').delete().eq('id', plot_id).execute()
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
        sb.table('plots').update(upd).eq('id', plot_id).execute()
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


# ----- Plot image (blob in Postgres: base64 in image_data) -----
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_PLOT_IMAGE_BYTES = 12 * 1024 * 1024  # 12MB raw upload limit before compression


@app.route('/api/plots/<int:plot_id>/image', methods=['GET'])
@require_auth
def get_plot_image(user_id, role, plot_id):
    """Serve plot image stored in Postgres (any access to panorama can view)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        pl = sb.table('plots').select('panorama_id, image_data, image_content_type').eq('id', plot_id).limit(1).execute()
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        row = pl.data[0]
        panorama_id = row['panorama_id']
    except Exception:
        return jsonify({'error': 'Not found'}), 404
    panorama, _ = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Forbidden'}), 403
    image_b64 = row.get('image_data')
    if not image_b64:
        return jsonify({'error': 'No image'}), 404
    try:
        data = base64.b64decode(image_b64)
    except Exception:
        return jsonify({'error': 'Invalid image data'}), 500
    content_type = row.get('image_content_type') or 'image/jpeg'

    # Auto-migrate legacy large/unoptimized images during read.
    try:
        compressed_b64, compressed_type, changed = maybe_compress_plot_image_base64(image_b64, content_type)
        if changed:
            content_type = compressed_type
            data = base64.b64decode(compressed_b64)
            sb.table('plots').update({
                'image_data': compressed_b64,
                'image_content_type': compressed_type,
                'updated_at': datetime.utcnow().isoformat(),
            }).eq('id', plot_id).execute()
    except Exception:
        # Serve existing image even if migration fails.
        pass

    response = Response(data, mimetype=content_type)
    response.headers['Cache-Control'] = 'private, max-age=86400'
    response.headers['X-Image-Optimized'] = '1'
    return response


@app.route('/api/plots/<int:plot_id>/image', methods=['POST', 'PUT'])
@require_auth
def upload_plot_image(user_id, role, plot_id):
    """Upload plot photo; store as base64 in Postgres."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    ext = (file.filename or '').rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({'error': 'File type not allowed. Use: png, jpg, jpeg, gif, webp'}), 400
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
        raw = file.read()
        if len(raw) > MAX_PLOT_IMAGE_BYTES:
            return jsonify({'error': f'Image too large (max {MAX_PLOT_IMAGE_BYTES // (1024*1024)}MB)'}), 400
        compressed_raw, content_type = compress_plot_image_bytes(raw, preferred_ext=ext)
        image_b64 = base64.b64encode(compressed_raw).decode('ascii')
        sb.table('plots').update({
            'image_data': image_b64,
            'image_content_type': content_type,
        }).eq('id', plot_id).execute()
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
        # Verify (if image_data column missing, run supabase_migration_plot_image.sql)
        check = sb.table('plots').select('image_data').eq('id', plot_id).limit(1).execute()
        if not check.data or not check.data[0].get('image_data'):
            return jsonify({
                'error': 'Image not saved. Run supabase_migration_plot_image.sql in Supabase SQL Editor.'
            }), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({
        'success': True,
        'content_type': content_type,
        'size_bytes': len(compressed_raw),
        'target_bytes': _target_plot_image_bytes(),
    })


@app.route('/api/markers/<marker_id>/image', methods=['GET'])
@require_auth
def get_marker_image(user_id, role, marker_id):
    """Fetch a single marker image and auto-migrate legacy oversized data."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    try:
        r = sb.table('plot_markers').select('id, plot_id, image_base64').eq('id', marker_id).limit(1).execute()
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

    image_value = row.get('image_base64') or ''
    if not image_value:
        return jsonify({'image_base64': None, 'optimized': False})

    optimized_value, changed = maybe_compress_marker_image_data(image_value)
    if changed:
        try:
            sb.table('plot_markers').update({
                'image_base64': optimized_value,
            }).eq('id', marker_id).execute()
            image_value = optimized_value
        except Exception:
            image_value = optimized_value

    return jsonify({'image_base64': image_value, 'optimized': bool(changed)})


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
