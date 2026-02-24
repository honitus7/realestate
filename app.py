"""
Real Estate Panorama Plot Marker - Flask Application
Uses Supabase for Auth + DB. All data is user-scoped; admin can grant client/viewer access.
"""
import os
import json
import uuid
import time
import re
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

from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, Response
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
    require_superadmin,
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
PAGE_ACCESS_TOKEN_TTL = max(60, int(os.environ.get('PAGE_ACCESS_TOKEN_TTL', '900')))

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


def _page_access_serializer():
    # Short-lived route token used to gate protected editor/client page renders.
    return URLSafeTimedSerializer(app.secret_key, salt='page-access')


def _issue_page_access_token(user_id, panorama_id, mode):
    return _page_access_serializer().dumps({
        'uid': str(user_id),
        'pid': int(panorama_id),
        'mode': str(mode or '').strip().lower(),
    })


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


def _read_uploaded_file_bytes(file_storage, max_bytes):
    """Read an uploaded FileStorage into memory with a hard size cap."""
    if not file_storage:
        return b''
    stream = getattr(file_storage, 'stream', None) or file_storage
    try:
        stream.seek(0)
    except Exception:
        pass
    # Read at most max_bytes + 1 so we can detect overflows without unbounded reads.
    cap = int(max_bytes) if max_bytes else 0
    if cap > 0:
        data = stream.read(cap + 1)
        if data and len(data) > cap:
            raise ValueError('file_too_large')
        return data or b''
    return stream.read() or b''


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


def _slugify_org_name(name):
    value = str(name or '').strip().lower()
    if not value:
        return 'org'
    value = re.sub(r'[^a-z0-9]+', '-', value)
    value = re.sub(r'-{2,}', '-', value).strip('-')
    return value or 'org'


def _get_org_name_and_slug_for_panorama(sb, panorama):
    """Return (org_name, org_slug) for a panorama row; falls back if schema isn't migrated yet."""
    org_name = None
    org_id = None
    try:
        org_id = panorama.get('org_id') if isinstance(panorama, dict) else None
    except Exception:
        org_id = None

    if sb and org_id:
        try:
            r = sb.table('organizations').select('name').eq('id', org_id).limit(1).execute()
            if r.data and len(r.data) > 0:
                org_name = r.data[0].get('name')
        except Exception:
            # Keep customer routes working even if organizations table doesn't exist yet.
            org_name = None

    org_name = str(org_name or 'PropMark')
    return org_name, _slugify_org_name(org_name)


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


def _is_workspace_schema_missing(exc):
    msg = str(exc).lower()
    if 'workspace_id' in msg and ('column' in msg or 'does not exist' in msg):
        return True
    if 'workspaces' in msg and ('relation' in msg or 'does not exist' in msg):
        return True
    if 'workspace_access' in msg and ('relation' in msg or 'does not exist' in msg):
        return True
    return False


def _workspace_schema_error_response():
    return jsonify({
        'error': 'Workspace schema missing. Run supabase_migration_workspaces.sql in Supabase SQL Editor.'
    }), 503


def _serialize_workspace_row(row, access_type='owner'):
    out = dict(row or {})
    if out.get('id') is not None:
        out['id'] = str(out.get('id'))
    if out.get('user_id') is not None:
        out['user_id'] = str(out.get('user_id'))
    if out.get('org_id') is not None:
        out['org_id'] = str(out.get('org_id'))
    for k in ('created_at', 'updated_at'):
        if out.get(k):
            out[k] = str(out.get(k))
    out['access_type'] = access_type
    return out


def _get_workspace_by_id(sb, workspace_id):
    r = sb.table('workspaces').select('*').eq('id', workspace_id).limit(1).execute()
    if r.data and len(r.data) > 0:
        return dict(r.data[0])
    return None


def _can_manage_workspace(sb, workspace, user_id, role):
    if not workspace:
        return False
    owner_id = str(workspace.get('user_id') or '')
    if owner_id == str(user_id):
        return True
    if role not in ('admin', 'superadmin'):
        return False
    if role == 'superadmin':
        return True
    # Non-superadmin admins are org-scoped.
    try:
        caller = get_profile(sb, user_id) or {}
        caller_org = caller.get('org_id')
    except Exception:
        caller_org = None
    workspace_org = workspace.get('org_id')
    return bool(caller_org and workspace_org and str(caller_org) == str(workspace_org))


def _load_panorama_for_page_mode(panorama_id, mode):
    """
    Validate short-lived page token (query param `pt`) and ensure the token owner
    still has required DB access for the requested panorama/mode.
    """
    sb = get_supabase()
    if not sb:
        return None, None, ("Database not configured", 503)

    token = str(request.args.get('pt') or '').strip()
    if not token:
        return None, None, ("Forbidden", 403)

    try:
        payload = _page_access_serializer().loads(token, max_age=int(PAGE_ACCESS_TOKEN_TTL))
    except SignatureExpired:
        return None, None, ("Page access token expired", 403)
    except BadSignature:
        return None, None, ("Invalid page access token", 403)

    token_mode = str(payload.get('mode') or '').strip().lower()
    if token_mode != str(mode or '').strip().lower():
        return None, None, ("Invalid page access token", 403)

    try:
        token_panorama_id = int(payload.get('pid'))
    except Exception:
        token_panorama_id = None
    if token_panorama_id != int(panorama_id):
        return None, None, ("Invalid page access token", 403)

    token_user_id = str(payload.get('uid') or '').strip()
    if not token_user_id:
        return None, None, ("Invalid page access token", 403)

    panorama, access_type = get_panorama_with_access(sb, panorama_id, token_user_id)
    if not panorama:
        return None, None, ("Panorama not found", 404)

    if token_mode == 'admin':
        if access_type != 'owner':
            return None, None, ("Forbidden", 403)
    elif token_mode == 'client':
        if access_type not in ('owner', 'client'):
            return None, None, ("Forbidden", 403)
    else:
        return None, None, ("Invalid page mode", 400)

    return sb, panorama, None


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

@app.route('/crm')
def crm_page():
    """Client CRM page for managing customer buy interests."""
    return render_template('crm.html', **auth_ctx())

@app.route('/organizations')
def organizations_page():
    """SuperAdmin org management page (client-side guard; API enforces permissions)."""
    return render_template('organizations.html', **auth_ctx())


@app.route('/users')
def users_page():
    return render_template('add_user.html', **auth_ctx())


@app.route('/panorama/<int:panorama_id>/access')
def panorama_access_page(panorama_id):
    """Manage who has client/viewer access to this panorama (admin or owner)."""
    return render_template('panorama_access.html', panorama_id=panorama_id, **auth_ctx())


@app.route('/workspace/<workspace_id>/access')
def workspace_access_page(workspace_id):
    """Manage who has client/viewer access to a workspace folder (admin or owner)."""
    return render_template('workspace_access.html', workspace_id=workspace_id, **auth_ctx())


@app.route('/admin/<int:panorama_id>')
def admin(panorama_id):
    sb, panorama, err = _load_panorama_for_page_mode(panorama_id, 'admin')
    if err:
        return err
    org_name, org_slug = _get_org_name_and_slug_for_panorama(sb, panorama)
    return render_template('editor.html', panorama=panorama, mode='admin', org_name=org_name, org_slug=org_slug, **auth_ctx())


@app.route('/customer/<int:panorama_id>')
def customer(panorama_id):
    """Legacy customer URL (no org slug). Redirects to canonical org-scoped URL."""
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    _org_name, org_slug = _get_org_name_and_slug_for_panorama(sb, panorama)
    qs = request.query_string.decode('utf-8') if request.query_string else ''
    suffix = ('?' + qs) if qs else ''
    is_360 = bool((panorama or {}).get('is_360'))
    if is_360:
        return redirect(f"/customer/{org_slug}/3d/{panorama_id}{suffix}", code=302)
    return redirect(f"/customer/{org_slug}/{panorama_id}{suffix}", code=302)


@app.route('/customer/<org_slug>/<int:panorama_id>')
def customer_with_org(org_slug, panorama_id):
    """Canonical customer URL (public): /customer/<orgname>/<id>."""
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    org_name, canonical_slug = _get_org_name_and_slug_for_panorama(sb, panorama)
    qs = request.query_string.decode('utf-8') if request.query_string else ''
    suffix = ('?' + qs) if qs else ''
    if bool((panorama or {}).get('is_360')):
        return redirect(f"/customer/{canonical_slug}/3d/{panorama_id}{suffix}", code=302)
    if str(org_slug or '').lower() != str(canonical_slug).lower():
        return redirect(f"/customer/{canonical_slug}/{panorama_id}{suffix}", code=302)
    return render_template('viewer.html', panorama=panorama, org_name=org_name, org_slug=canonical_slug, **auth_ctx())


@app.route('/client/<int:panorama_id>')
def client(panorama_id):
    sb, panorama, err = _load_panorama_for_page_mode(panorama_id, 'client')
    if err:
        return err
    org_name, org_slug = _get_org_name_and_slug_for_panorama(sb, panorama)
    return render_template('client.html', panorama=panorama, mode='client', org_name=org_name, org_slug=org_slug, **auth_ctx())


@app.route('/admin/3d/<int:panorama_id>')
def admin_3d(panorama_id):
    sb, panorama, err = _load_panorama_for_page_mode(panorama_id, 'admin')
    if err:
        return err
    org_name, org_slug = _get_org_name_and_slug_for_panorama(sb, panorama)
    return render_template('admin_3d.html', panorama=panorama, org_name=org_name, org_slug=org_slug, **auth_ctx())


@app.route('/client/3d/<int:panorama_id>')
def client_3d(panorama_id):
    sb, panorama, err = _load_panorama_for_page_mode(panorama_id, 'client')
    if err:
        return err
    org_name, org_slug = _get_org_name_and_slug_for_panorama(sb, panorama)
    return render_template('client_3d.html', panorama=panorama, org_name=org_name, org_slug=org_slug, **auth_ctx())


@app.route('/customer/3d/<int:panorama_id>')
def customer_3d(panorama_id):
    """Legacy customer 360 URL (no org slug). Redirects to canonical org-scoped URL."""
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    _org_name, org_slug = _get_org_name_and_slug_for_panorama(sb, panorama)
    qs = request.query_string.decode('utf-8') if request.query_string else ''
    suffix = ('?' + qs) if qs else ''
    is_360 = bool((panorama or {}).get('is_360'))
    if not is_360:
        return redirect(f"/customer/{org_slug}/{panorama_id}{suffix}", code=302)
    return redirect(f"/customer/{org_slug}/3d/{panorama_id}{suffix}", code=302)


@app.route('/customer/<org_slug>/3d/<int:panorama_id>')
def customer_3d_with_org(org_slug, panorama_id):
    """Canonical customer 360 URL (public): /customer/<orgname>/3d/<id>."""
    sb = get_supabase()
    if not sb:
        return "Database not configured", 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return "Panorama not found", 404
    org_name, canonical_slug = _get_org_name_and_slug_for_panorama(sb, panorama)
    qs = request.query_string.decode('utf-8') if request.query_string else ''
    suffix = ('?' + qs) if qs else ''
    if not bool((panorama or {}).get('is_360')):
        return redirect(f"/customer/{canonical_slug}/{panorama_id}{suffix}", code=302)
    if str(org_slug or '').lower() != str(canonical_slug).lower():
        return redirect(f"/customer/{canonical_slug}/3d/{panorama_id}{suffix}", code=302)
    return render_template('customer_3d.html', panorama=panorama, org_name=org_name, org_slug=canonical_slug, **auth_ctx())


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


# ----- API: public (no auth) -----

@app.route('/api/public/panoramas/<int:panorama_id>', methods=['GET'])
def public_get_panorama(panorama_id):
    """Public panorama metadata for customer share links (no auth)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    out = dict(panorama)
    # Do not expose owner user_id in public responses.
    out.pop('user_id', None)
    for k in ('created_at', 'updated_at'):
        if k in out and out[k]:
            out[k] = str(out[k])
    return jsonify(out)


@app.route('/api/public/panoramas/<int:panorama_id>/plots', methods=['GET'])
def public_get_plots(panorama_id):
    """Public plots for customer share links (no auth)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama = get_panorama_by_id(sb, panorama_id)
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
            # Frontend loads image by id; use filename presence as a lightweight has_image flag.
            p['has_image'] = bool(p.get('image_filename'))
            p.pop('image_filename', None)
            plots.append(p)
        return jsonify(plots)
    except Exception as e:
        msg = str(e)
        if 'image_filename' in msg.lower() and ('does not exist' in msg.lower() or 'column' in msg.lower()):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500


@app.route('/api/public/plots/<int:plot_id>/image', methods=['GET'])
def public_get_plot_image(plot_id):
    """Public plot image (S3) for customer share links (no auth)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        try:
            pl = sb.table('plots').select('panorama_id, image_filename, image_content_type').eq('id', plot_id).limit(1).execute()
        except Exception as e:
            # Backward-compatible: image_content_type column may not exist.
            if 'image_content_type' in str(e).lower():
                pl = sb.table('plots').select('panorama_id, image_filename').eq('id', plot_id).limit(1).execute()
            else:
                raise
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        row = pl.data[0]
        panorama_id = row.get('panorama_id')
        try:
            panorama_id = int(panorama_id)
        except Exception:
            panorama_id = None
    except Exception as e:
        msg = str(e).lower()
        if ('image_filename' in msg or 'image_content_type' in msg) and ('does not exist' in msg or 'column' in msg):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': 'Not found'}), 404

    if panorama_id is None or not get_panorama_by_id(sb, panorama_id):
        # Hide existence if the plot points to a missing panorama.
        return jsonify({'error': 'Not found'}), 404

    image_filename = row.get('image_filename') or ''
    if not image_filename:
        return jsonify({'error': 'No image'}), 404

    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not configured'}), 503

    try:
        obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=_plot_object_key(image_filename))
        body = obj.get('Body')
        data = body.read() if body else b''
        try:
            if body:
                body.close()
        except Exception:
            pass
        content_type = obj.get('ContentType') or row.get('image_content_type') or 'image/jpeg'
    except Exception:
        return jsonify({'error': 'Image not found'}), 404

    resp = Response(data, mimetype=str(content_type or 'image/jpeg'))
    resp.headers['Cache-Control'] = 'private, max-age=900'
    return resp


@app.route('/api/public/panoramas/<int:panorama_id>/markers', methods=['GET'])
def public_list_markers(panorama_id):
    """Public 360 markers (hotspots) for a panorama (no auth)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404

    style_columns_supported = True
    link_columns_supported = True
    image_columns_supported = True

    base_cols = [
        'id', 'plot_id', 'name', 'description',
        'longitude', 'latitude', 'status', 'created_at'
    ]
    style_cols = ['marker_style', 'marker_icon', 'marker_color']
    link_cols = ['linked_panorama_id']

    def build_columns():
        cols = list(base_cols)
        if image_columns_supported:
            cols.insert(4, 'image_filename')
        if link_columns_supported:
            cols.extend(link_cols)
        if style_columns_supported:
            cols.extend(style_cols)
        return ', '.join(cols)

    def is_style_column_error(err):
        msg = str(err).lower()
        return 'marker_style' in msg or 'marker_icon' in msg or 'marker_color' in msg

    def is_link_column_error(err):
        msg = str(err).lower()
        return 'linked_panorama_id' in msg or 'linked panorama' in msg

    def is_image_column_error(err):
        msg = str(err).lower()
        return 'image_filename' in msg

    last_error = None
    for _ in range(3):
        try:
            r = (
                sb.table('plot_markers')
                .select(build_columns())
                .eq('plot_id', str(panorama_id))
                .order('created_at', desc=False)
                .execute()
            )
            return jsonify(r.data or [])
        except Exception as e:
            last_error = e
            changed = False
            if style_columns_supported and is_style_column_error(e):
                style_columns_supported = False
                changed = True
            if link_columns_supported and is_link_column_error(e):
                link_columns_supported = False
                changed = True
            if image_columns_supported and is_image_column_error(e):
                image_columns_supported = False
                changed = True
            if not changed:
                break

    return jsonify({'error': str(last_error or 'Failed to load markers')}), 500


@app.route('/api/public/markers/<marker_id>/image', methods=['GET'])
def public_get_marker_image(marker_id):
    """Public marker image (S3) for customer share links (no auth)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    try:
        r = sb.table('plot_markers').select('id, plot_id, image_filename').eq('id', marker_id).limit(1).execute()
        if not r.data:
            return jsonify({'error': 'Marker not found'}), 404
        row = r.data[0]
    except Exception as e:
        msg = str(e).lower()
        if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': 'Marker not found'}), 404

    plot_id = str(row.get('plot_id') or '').strip()
    # If this marker belongs to a panorama by id, ensure the panorama exists.
    try:
        panorama_id = int(plot_id)
    except Exception:
        panorama_id = None
    if panorama_id is not None and not get_panorama_by_id(sb, panorama_id):
        return jsonify({'error': 'Not found'}), 404

    image_filename = row.get('image_filename') or ''
    if not image_filename:
        return jsonify({'error': 'No image'}), 404
    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    try:
        obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=_marker_object_key(image_filename))
        body = obj.get('Body')
        data = body.read() if body else b''
        try:
            if body:
                body.close()
        except Exception:
            pass
        content_type = obj.get('ContentType') or 'image/jpeg'
    except Exception:
        return jsonify({'error': 'Image not found'}), 404
    resp = Response(data, mimetype=str(content_type or 'image/jpeg'))
    resp.headers['Cache-Control'] = 'private, max-age=900'
    return resp


@app.route('/api/public/buy-interests', methods=['POST'])
def public_create_buy_interest():
    """Create a buy interest from a public customer page (no auth)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    data = request.get_json(silent=True) or {}
    panorama_id = data.get('panorama_id')
    try:
        panorama_id = int(panorama_id)
    except Exception:
        panorama_id = None

    customer_name = str(data.get('customer_name') or data.get('name') or '').strip()
    customer_email = str(data.get('customer_email') or data.get('email') or '').strip()
    customer_phone = str(data.get('customer_phone') or data.get('phone') or '').strip()
    category = str(data.get('category') or '').strip()

    items = data.get('items') or data.get('plots') or []
    if not isinstance(items, list):
        items = []

    if not panorama_id:
        return jsonify({'error': 'panorama_id is required'}), 400
    if not customer_name:
        return jsonify({'error': 'Name is required'}), 400
    if not customer_email or '@' not in customer_email:
        return jsonify({'error': 'Valid email is required'}), 400
    if not customer_phone:
        return jsonify({'error': 'Contact number is required'}), 400
    if not category:
        return jsonify({'error': 'Category is required'}), 400

    panorama = get_panorama_by_id(sb, panorama_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404

    plot_ids = []
    for item in items:
        if isinstance(item, (int, float, str)):
            raw = item
        elif isinstance(item, dict):
            raw = item.get('plot_id') if 'plot_id' in item else item.get('id')
        else:
            raw = None
        if raw is None or str(raw).strip() == '':
            continue
        try:
            plot_ids.append(int(raw))
        except Exception:
            continue
    plot_ids = list(dict.fromkeys(plot_ids))
    if not plot_ids:
        return jsonify({'error': 'At least one plot is required'}), 400

    plots_snapshot = []
    try:
        r = (
            sb.table('plots')
            .select('id, panorama_id, name, area, price, status')
            .in_('id', plot_ids)
            .eq('panorama_id', panorama_id)
            .execute()
        )
        found = {int(row.get('id')): row for row in (r.data or []) if row and row.get('id') is not None}
        for pid in plot_ids:
            row = found.get(int(pid))
            if not row:
                continue
            plots_snapshot.append({
                'plot_id': int(row.get('id')),
                'name': row.get('name') or '',
                'area': row.get('area') or '',
                'price': row.get('price') or '',
                'status': row.get('status') or '',
            })
    except Exception as e:
        msg = str(e)
        if 'buy_interests' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'buy_interests table not found. Run supabase_migration_buy_interests.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500

    if not plots_snapshot:
        return jsonify({'error': 'No valid plots found for this panorama'}), 400

    now = datetime.utcnow().isoformat()
    insert_row = {
        'panorama_id': panorama_id,
        'submitted_by': None,
        'customer_name': customer_name,
        'customer_email': customer_email,
        'customer_phone': customer_phone,
        'category': category,
        'plots': plots_snapshot,
        'status': 'new',
        'notes': '',
        'created_at': now,
        'updated_at': now,
    }

    try:
        r = sb.table('buy_interests').insert(insert_row).execute()
        row = (r.data or [None])[0] if hasattr(r, 'data') else None
        return jsonify({'success': True, 'buy_interest': row or insert_row}), 201
    except Exception as e:
        msg = str(e)
        if 'buy_interests' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'buy_interests table not found. Run supabase_migration_buy_interests.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500


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
        if 'workspace_id' in o and o.get('workspace_id'):
            o['workspace_id'] = str(o['workspace_id'])
        out.append(o)
    return jsonify(out)


@app.route('/api/panoramas/<int:panorama_id>/page-token', methods=['POST'])
@require_auth
def create_panorama_page_token(user_id, role, panorama_id):
    """Issue a short-lived token for opening protected admin/client pages."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    payload = request.get_json(silent=True) or {}
    mode = str(payload.get('mode') or '').strip().lower()
    if mode not in ('admin', 'client'):
        return jsonify({'error': 'mode must be admin or client'}), 400

    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404

    if mode == 'admin':
        if access_type != 'owner':
            return jsonify({'error': 'Only owner can open admin mode'}), 403
    else:
        if access_type not in ('owner', 'client'):
            return jsonify({'error': 'Only owner or client can open client mode'}), 403

    token = _issue_page_access_token(user_id, panorama_id, mode)
    return jsonify({
        'token': token,
        'mode': mode,
        'panorama_id': int(panorama_id),
        'expires_in': int(PAGE_ACCESS_TOKEN_TTL),
    })


@app.route('/api/workspaces', methods=['GET'])
@require_auth
def list_workspaces(user_id, role):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        owned = (
            sb.table('workspaces')
            .select('id, user_id, org_id, name, created_at, updated_at')
            .eq('user_id', user_id)
            .order('updated_at', desc=True)
            .execute()
        )
        shared_acc = (
            sb.table('workspace_access')
            .select('workspace_id, access_type')
            .eq('user_id', user_id)
            .execute()
        )
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500

    out = []
    by_id = {}

    for row in (owned.data or []):
        item = _serialize_workspace_row(row, 'owner')
        by_id[item['id']] = item
        out.append(item)

    shared_map = {}
    for row in (shared_acc.data or []):
        wsid = row.get('workspace_id')
        if not wsid:
            continue
        wsid = str(wsid)
        if wsid in by_id:
            continue
        shared_map[wsid] = str(row.get('access_type') or 'viewer')

    shared_ids = list(shared_map.keys())
    if shared_ids:
        try:
            shared_rows = (
                sb.table('workspaces')
                .select('id, user_id, org_id, name, created_at, updated_at')
                .in_('id', shared_ids)
                .execute()
            )
            for row in (shared_rows.data or []):
                wsid = str(row.get('id'))
                access_type = shared_map.get(wsid, 'viewer')
                item = _serialize_workspace_row(row, access_type)
                by_id[wsid] = item
                out.append(item)
        except Exception as e:
            if _is_workspace_schema_missing(e):
                return _workspace_schema_error_response()
            return jsonify({'error': str(e)}), 500

    # Count only panoramas this user can access to avoid exposing folder inventory
    # beyond granted scope.
    counts = {}
    for pano in (list_panoramas_for_user(sb, user_id) or []):
        wsid = pano.get('workspace_id') if isinstance(pano, dict) else None
        if not wsid:
            continue
        key = str(wsid)
        counts[key] = int(counts.get(key, 0)) + 1
    for row in out:
        row['panorama_count'] = int(counts.get(str(row.get('id')), 0))

    # For shared folders, only return folders that currently expose at least one panorama.
    # This avoids showing stale/empty shared folders to client accounts.
    out = [
        row for row in out
        if str(row.get('access_type') or 'viewer') == 'owner'
        or int(row.get('panorama_count') or 0) > 0
    ]

    out.sort(key=lambda x: ((x.get('access_type') != 'owner'), str(x.get('name') or '').lower()))
    return jsonify(out)


@app.route('/api/workspaces', methods=['POST'])
@require_auth
def create_workspace(user_id, role):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    payload = request.get_json(silent=True) or request.form or {}
    name = str(payload.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    if len(name) > 120:
        return jsonify({'error': 'name must be 120 characters or fewer'}), 400
    now = datetime.utcnow().isoformat()
    org_id = None
    try:
        profile = get_profile(sb, user_id) or {}
        org_id = profile.get('org_id')
    except Exception:
        org_id = None
    row = {
        'user_id': user_id,
        'org_id': org_id,
        'name': name,
        'created_at': now,
        'updated_at': now,
    }
    try:
        r = sb.table('workspaces').insert(row).execute()
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        msg = str(e).lower()
        if 'duplicate' in msg or 'unique' in msg or 'already exists' in msg:
            return jsonify({'error': 'Workspace name already exists'}), 409
        return jsonify({'error': str(e)}), 500
    created = (r.data or [None])[0]
    return jsonify({'success': True, 'workspace': _serialize_workspace_row(created or row, 'owner')}), 201


@app.route('/api/workspaces/<workspace_id>', methods=['PATCH', 'PUT'])
@require_auth
def rename_workspace(user_id, role, workspace_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    payload = request.get_json(silent=True) or request.form or {}
    name = str(payload.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    if len(name) > 120:
        return jsonify({'error': 'name must be 120 characters or fewer'}), 400
    try:
        workspace = _get_workspace_by_id(sb, workspace_id)
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    if str(workspace.get('user_id') or '') != str(user_id):
        return jsonify({'error': 'Only workspace owner can rename'}), 403
    try:
        r = (
            sb.table('workspaces')
            .update({'name': name, 'updated_at': datetime.utcnow().isoformat()})
            .eq('id', workspace_id)
            .execute()
        )
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        msg = str(e).lower()
        if 'duplicate' in msg or 'unique' in msg or 'already exists' in msg:
            return jsonify({'error': 'Workspace name already exists'}), 409
        return jsonify({'error': str(e)}), 500
    row = (r.data or [None])[0] if hasattr(r, 'data') else None
    return jsonify({'success': True, 'workspace': _serialize_workspace_row(row or {'id': workspace_id, 'name': name, 'user_id': user_id}, 'owner')})


@app.route('/api/workspaces/<workspace_id>', methods=['DELETE'])
@require_auth
def delete_workspace(user_id, role, workspace_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        workspace = _get_workspace_by_id(sb, workspace_id)
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    if str(workspace.get('user_id') or '') != str(user_id):
        return jsonify({'error': 'Only workspace owner can delete'}), 403
    try:
        sb.table('workspaces').delete().eq('id', workspace_id).execute()
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/workspaces/<workspace_id>/access', methods=['GET'])
@require_auth
def get_workspace_access(user_id, role, workspace_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        workspace = _get_workspace_by_id(sb, workspace_id)
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    if not _can_manage_workspace(sb, workspace, user_id, role):
        return jsonify({'error': 'Only owner or admin can list access'}), 403
    try:
        r = sb.table('workspace_access').select('*').eq('workspace_id', workspace_id).execute()
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    out = []
    for row in (r.data or []):
        item = dict(row)
        if item.get('user_id'):
            item['user_id'] = str(item.get('user_id'))
        if item.get('workspace_id'):
            item['workspace_id'] = str(item.get('workspace_id'))
        if item.get('granted_by'):
            item['granted_by'] = str(item.get('granted_by'))
        if item.get('created_at'):
            item['created_at'] = str(item.get('created_at'))
        out.append(item)
    return jsonify(out)


@app.route('/api/workspaces/<workspace_id>/access', methods=['POST'])
@require_auth
def grant_workspace_access(user_id, role, workspace_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    data = request.get_json(silent=True) or {}
    target_user_id = str(data.get('user_id') or '').strip()
    access_type = str(data.get('access_type') or 'viewer').strip().lower()
    if not target_user_id:
        return jsonify({'error': 'user_id is required'}), 400
    if access_type not in ('client', 'viewer'):
        return jsonify({'error': 'access_type must be client or viewer'}), 400
    if str(target_user_id) == str(user_id):
        return jsonify({'error': 'Cannot grant access to yourself'}), 400
    try:
        workspace = _get_workspace_by_id(sb, workspace_id)
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    if not _can_manage_workspace(sb, workspace, user_id, role):
        return jsonify({'error': 'Only owner or admin can grant access'}), 403

    workspace_org = workspace.get('org_id')
    if workspace_org and role != 'superadmin':
        caller = get_profile(sb, user_id) or {}
        target = get_profile(sb, target_user_id) or {}
        caller_org = caller.get('org_id')
        target_org = target.get('org_id')
        if not caller_org or not target_org:
            return jsonify({'error': 'User organization is not set. Ask a SuperAdmin to assign orgs.'}), 403
        if str(caller_org) != str(workspace_org) or str(target_org) != str(workspace_org):
            return jsonify({'error': 'You can only grant access to users in your organization'}), 403

    try:
        sb.table('workspace_access').upsert({
            'workspace_id': workspace_id,
            'user_id': target_user_id,
            'access_type': access_type,
            'granted_by': user_id,
        }, on_conflict='workspace_id,user_id').execute()
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True}), 201


@app.route('/api/workspaces/<workspace_id>/access/<target_user_id>', methods=['DELETE'])
@require_auth
def revoke_workspace_access(user_id, role, workspace_id, target_user_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        workspace = _get_workspace_by_id(sb, workspace_id)
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    if not _can_manage_workspace(sb, workspace, user_id, role):
        return jsonify({'error': 'Only owner or admin can revoke access'}), 403
    try:
        sb.table('workspace_access').delete().eq('workspace_id', workspace_id).eq('user_id', target_user_id).execute()
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


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
    workspace_id = str(payload.get('workspace_id') or '').strip() or None
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
        org_id = None
        try:
            profile = get_profile(sb, user_id) or {}
            org_id = profile.get('org_id')
        except Exception:
            org_id = None

        if workspace_id:
            try:
                workspace = _get_workspace_by_id(sb, workspace_id)
            except Exception as e:
                if _is_workspace_schema_missing(e):
                    return _workspace_schema_error_response()
                return jsonify({'error': str(e)}), 500
            if not workspace:
                return jsonify({'error': 'Workspace not found'}), 404
            if str(workspace.get('user_id') or '') != str(user_id):
                return jsonify({'error': 'You can only upload into your own workspace'}), 403
            ws_org = workspace.get('org_id')
            if org_id and ws_org and str(org_id) != str(ws_org):
                return jsonify({'error': 'Workspace organization does not match your profile'}), 403

        insert_row = {
            'user_id': user_id,
            'org_id': org_id,
            'workspace_id': workspace_id,
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
        except Exception as e:
            # Backward-compatible: org_id column may not exist yet.
            msg = str(e).lower()
            if (('workspace_id' in msg and ('column' in msg or 'does not exist' in msg)) or ('org_id' in msg)):
                retry_row = dict(insert_row)
                if 'workspace_id' in msg and ('column' in msg or 'does not exist' in msg):
                    retry_row.pop('workspace_id', None)
                if 'org_id' in msg:
                    retry_row.pop('org_id', None)
                try:
                    r = sb.table('panoramas').insert(retry_row).execute()
                    insert_row = retry_row
                except Exception as e2:
                    msg2 = str(e2).lower()
                    changed = False
                    if 'workspace_id' in msg2 and ('column' in msg2 or 'does not exist' in msg2):
                        retry_row.pop('workspace_id', None)
                        changed = True
                    if 'org_id' in msg2:
                        retry_row.pop('org_id', None)
                        changed = True
                    if not changed:
                        raise
                    r = sb.table('panoramas').insert(retry_row).execute()
                    insert_row = retry_row
            else:
                raise
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
            'workspace_id': workspace_id,
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


@app.route('/api/panoramas/<int:panorama_id>/workspace', methods=['PATCH', 'PUT'])
@require_auth
def move_panorama_to_workspace(user_id, role, panorama_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    if access_type != 'owner':
        return jsonify({'error': 'Only owner can move this panorama'}), 403

    payload = request.get_json(silent=True) or request.form or {}
    workspace_id = payload.get('workspace_id')
    workspace_id = None if workspace_id in (None, '', 'null') else str(workspace_id).strip()

    if workspace_id:
        try:
            workspace = _get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if _is_workspace_schema_missing(e):
                return _workspace_schema_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        if str(workspace.get('user_id') or '') != str(user_id):
            return jsonify({'error': 'You can only move to your own workspace'}), 403
        panorama_org = panorama.get('org_id')
        workspace_org = workspace.get('org_id')
        if panorama_org and workspace_org and str(panorama_org) != str(workspace_org):
            return jsonify({'error': 'Workspace organization mismatch'}), 403

    try:
        r = (
            sb.table('panoramas')
            .update({'workspace_id': workspace_id, 'updated_at': datetime.utcnow().isoformat()})
            .eq('id', panorama_id)
            .execute()
        )
    except Exception as e:
        if _is_workspace_schema_missing(e):
            return _workspace_schema_error_response()
        return jsonify({'error': str(e)}), 500

    row = (r.data or [None])[0] if hasattr(r, 'data') else None
    current_workspace_id = (row or {}).get('workspace_id')
    return jsonify({
        'success': True,
        'panorama': {
            'id': panorama_id,
            'workspace_id': str(current_workspace_id or workspace_id) if (current_workspace_id or workspace_id) else None,
            'updated_at': str((row or {}).get('updated_at') or datetime.utcnow().isoformat()),
        }
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
MAX_PLOT_IMAGE_BYTES = int(os.environ.get('MAX_PLOT_IMAGE_BYTES', str(25 * 1024 * 1024)))  # default 25MB
MAX_MARKER_IMAGE_BYTES = int(os.environ.get('MAX_MARKER_IMAGE_BYTES', str(5 * 1024 * 1024)))  # default 5MB


@app.route('/api/plots/<int:plot_id>/image', methods=['GET'])
@require_auth
def get_plot_image(user_id, role, plot_id):
    """Serve plot image from S3 (any access to panorama can view)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        try:
            pl = sb.table('plots').select('panorama_id, image_filename, image_content_type').eq('id', plot_id).limit(1).execute()
        except Exception as e:
            # Backward-compatible: image_content_type column may not exist.
            if 'image_content_type' in str(e).lower():
                pl = sb.table('plots').select('panorama_id, image_filename').eq('id', plot_id).limit(1).execute()
            else:
                raise
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        row = pl.data[0]
        panorama_id = row['panorama_id']
    except Exception as e:
        msg = str(e).lower()
        if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': 'Not found'}), 404
    panorama, _ = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Forbidden'}), 403
    image_filename = row.get('image_filename') or ''
    if not image_filename:
        return jsonify({'error': 'No image'}), 404
    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    try:
        obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=_plot_object_key(image_filename))
        body = obj.get('Body')
        data = body.read() if body else b''
        try:
            if body:
                body.close()
        except Exception:
            pass
        content_type = obj.get('ContentType') or row.get('image_content_type') or 'image/jpeg'
    except Exception:
        return jsonify({'error': 'Image not found'}), 404
    resp = Response(data, mimetype=str(content_type or 'image/jpeg'))
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
    """Upload or finalize a plot image upload (S3 only)."""
    if not _use_s3_for_panoramas():
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    try:
        pl = sb.table('plots').select('panorama_id, image_filename').eq('id', plot_id).limit(1).execute()
        if not pl.data or len(pl.data) == 0:
            return jsonify({'error': 'Plot not found'}), 404
        panorama_id = pl.data[0]['panorama_id']
        old_filename = str(pl.data[0].get('image_filename') or '').strip()
    except Exception as e:
        msg = str(e).lower()
        if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': 'Not found'}), 404
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama or not can_edit_plots(access_type):
        return jsonify({'error': 'Forbidden'}), 403

    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not fully configured'}), 503

    # Accept direct file uploads as a robust fallback (still stored in S3).
    if request.files.get('file'):
        file = request.files.get('file')
        original_filename = str(getattr(file, 'filename', '') or '').strip()
        if not original_filename:
            return jsonify({'error': 'file is required'}), 400

        content_type_hint = str(getattr(file, 'mimetype', '') or '').lower().strip()
        ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
        if ext not in ALLOWED_IMAGE_EXTENSIONS and content_type_hint:
            ext = _detect_ext_from_content_type(content_type_hint)
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            return jsonify({'error': 'File type not allowed. Use: png, jpg, jpeg, gif, webp'}), 400

        try:
            raw = _read_uploaded_file_bytes(file, MAX_PLOT_IMAGE_BYTES)
        except ValueError as ve:
            if str(ve) == 'file_too_large':
                return jsonify({'error': f'Image too large (max {MAX_PLOT_IMAGE_BYTES // (1024*1024)}MB)'}), 413
            return jsonify({'error': 'Invalid upload'}), 400

        unique = uuid.uuid4().hex[:10]
        filename = f"plot_{plot_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{unique}.{ext}"
        content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
        try:
            # Streaming upload when possible.
            if hasattr(client, 'put_object'):
                client.put_object(
                    Bucket=SUPABASE_S3_BUCKET,
                    Key=_plot_object_key(filename),
                    Body=raw,
                    ContentType=content_type,
                )
        except Exception as e:
            app.logger.exception('Direct plot image upload failed')
            return jsonify({'error': str(e) or 'Upload failed'}), 500

        try:
            try:
                sb.table('plots').update({
                    'image_filename': filename,
                    'image_content_type': content_type,
                }).eq('id', plot_id).execute()
            except Exception as ee:
                # Backward-compatible: image_content_type column may not exist.
                if 'image_content_type' in str(ee).lower():
                    sb.table('plots').update({
                        'image_filename': filename,
                    }).eq('id', plot_id).execute()
                else:
                    raise
            sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
        except Exception as e:
            msg = str(e).lower()
            if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
                return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': str(e)}), 500

        if old_filename and old_filename != filename:
            _delete_plot_from_s3(old_filename)
        return jsonify({'success': True, 'filename': filename})

    payload = request.form or {}
    filename = os.path.basename(str(payload.get('filename') or '')).strip()
    upload_token = str(payload.get('upload_token') or '').strip()
    if not filename:
        return jsonify({'error': 'filename is required'}), 400
    if not upload_token:
        return jsonify({'error': 'upload_token is required'}), 400

    try:
        signed = _plot_upload_serializer().loads(upload_token, max_age=int(SUPABASE_S3_UPLOAD_URL_TTL))
    except SignatureExpired:
        return jsonify({'error': 'upload_token expired; request a new upload URL'}), 400
    except BadSignature:
        return jsonify({'error': 'Invalid upload_token'}), 400

    if str(signed.get('user_id')) != str(user_id) or int(signed.get('plot_id')) != int(plot_id) or str(signed.get('filename')) != filename:
        return jsonify({'error': 'Invalid upload_token'}), 403

    head = None
    for attempt in range(5):
        try:
            head = client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=_plot_object_key(filename))
            break
        except Exception:
            if attempt < 4:
                time.sleep(0.15 * (attempt + 1))
                continue
            return jsonify({'error': 'Upload not found. Re-upload and try again.'}), 400

    try:
        size_bytes = int(head.get('ContentLength') or 0)
    except Exception:
        size_bytes = 0
    if size_bytes and size_bytes > MAX_PLOT_IMAGE_BYTES:
        _delete_plot_from_s3(filename)
        return jsonify({'error': f'Image too large (max {MAX_PLOT_IMAGE_BYTES // (1024*1024)}MB)'}), 413

    try:
        try:
            sb.table('plots').update({
                'image_filename': filename,
                'image_content_type': str(signed.get('content_type') or ''),
            }).eq('id', plot_id).execute()
        except Exception as ee:
            # Backward-compatible: image_content_type column may not exist.
            if 'image_content_type' in str(ee).lower():
                sb.table('plots').update({
                    'image_filename': filename,
                }).eq('id', plot_id).execute()
            else:
                raise
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
    except Exception as e:
        msg = str(e).lower()
        if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': str(e)}), 500

    if old_filename and old_filename != filename:
        _delete_plot_from_s3(old_filename)

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
    except Exception as e:
        msg = str(e).lower()
        if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
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
    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    try:
        obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=_marker_object_key(image_filename))
        body = obj.get('Body')
        data = body.read() if body else b''
        try:
            if body:
                body.close()
        except Exception:
            pass
        content_type = obj.get('ContentType') or 'image/jpeg'
    except Exception:
        return jsonify({'error': 'Image not found'}), 404
    resp = Response(data, mimetype=str(content_type or 'image/jpeg'))
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
    """Upload or finalize a marker image upload (S3 only)."""
    if not _use_s3_for_panoramas():
        return jsonify({'error': 'Supabase S3 is not configured'}), 503
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    try:
        r = sb.table('plot_markers').select('id, plot_id, image_filename').eq('id', marker_id).limit(1).execute()
        if not r.data:
            return jsonify({'error': 'Marker not found'}), 404
        row = r.data[0]
        old_filename = str(row.get('image_filename') or '').strip()
    except Exception as e:
        msg = str(e).lower()
        if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
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

    client = _get_s3_client()
    if not client:
        return jsonify({'error': 'Supabase S3 is not fully configured'}), 503

    # Accept direct file uploads as a robust fallback (still stored in S3).
    if request.files.get('file'):
        file = request.files.get('file')
        original_filename = str(getattr(file, 'filename', '') or '').strip()
        if not original_filename:
            return jsonify({'error': 'file is required'}), 400

        content_type_hint = str(getattr(file, 'mimetype', '') or '').lower().strip()
        ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
        if ext not in ALLOWED_IMAGE_EXTENSIONS and content_type_hint:
            ext = _detect_ext_from_content_type(content_type_hint)
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            return jsonify({'error': 'File type not allowed. Use: png, jpg, jpeg, gif, webp'}), 400

        try:
            raw = _read_uploaded_file_bytes(file, MAX_MARKER_IMAGE_BYTES)
        except ValueError as ve:
            if str(ve) == 'file_too_large':
                return jsonify({'error': f'Image too large (max {MAX_MARKER_IMAGE_BYTES // (1024*1024)}MB)'}), 413
            return jsonify({'error': 'Invalid upload'}), 400

        unique = uuid.uuid4().hex[:10]
        filename = f"marker_{marker_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{unique}.{ext}"
        content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')

        try:
            client.put_object(
                Bucket=SUPABASE_S3_BUCKET,
                Key=_marker_object_key(filename),
                Body=raw,
                ContentType=content_type,
            )
        except Exception as e:
            app.logger.exception('Direct marker image upload failed')
            return jsonify({'error': str(e) or 'Upload failed'}), 500

        try:
            sb.table('plot_markers').update({
                'image_filename': filename,
            }).eq('id', marker_id).execute()
        except Exception as e:
            msg = str(e).lower()
            if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
                return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': str(e)}), 500

        if old_filename and old_filename != filename:
            _delete_marker_from_s3(old_filename)
        return jsonify({'success': True, 'filename': filename})

    payload = request.form or {}
    filename = os.path.basename(str(payload.get('filename') or '')).strip()
    upload_token = str(payload.get('upload_token') or '').strip()
    if not filename:
        return jsonify({'error': 'filename is required'}), 400
    if not upload_token:
        return jsonify({'error': 'upload_token is required'}), 400

    try:
        signed = _marker_upload_serializer().loads(upload_token, max_age=int(SUPABASE_S3_UPLOAD_URL_TTL))
    except SignatureExpired:
        return jsonify({'error': 'upload_token expired; request a new upload URL'}), 400
    except BadSignature:
        return jsonify({'error': 'Invalid upload_token'}), 400

    if str(signed.get('user_id')) != str(user_id) or str(signed.get('marker_id')) != str(marker_id) or str(signed.get('filename')) != filename:
        return jsonify({'error': 'Invalid upload_token'}), 403

    head = None
    for attempt in range(5):
        try:
            head = client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=_marker_object_key(filename))
            break
        except Exception:
            if attempt < 4:
                time.sleep(0.15 * (attempt + 1))
                continue
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
        msg = str(e).lower()
        if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
            return jsonify({'error': 'image_filename column missing. Run supabase_migration_image_s3.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': str(e)}), 500

    if old_filename and old_filename != filename:
        _delete_marker_from_s3(old_filename)

    return jsonify({'success': True})


# ----- Admin: create user + set as user in profiles -----
@app.route('/api/admin/users', methods=['POST'])
@require_auth
def admin_create_user(user_id, role):
    if role not in ('admin', 'superadmin'):
        return jsonify({'error': 'Only admins can create users'}), 403
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return jsonify({'error': 'Server not configured for creating users'}), 503
    data = request.get_json() or {}
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''
    display_name = (data.get('display_name') or '').strip() or None
    requested_role = str(data.get('role') or 'user').strip().lower()
    if requested_role not in ('admin', 'user', 'superadmin'):
        requested_role = 'user'
    if requested_role == 'superadmin' and role != 'superadmin':
        return jsonify({'error': 'Only superadmins can create superadmin users'}), 403
    as_admin = requested_role == 'admin'
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
        org_id = None
        try:
            creator_profile = get_profile(sb, user_id) or {}
            creator_org_id = creator_profile.get('org_id')
        except Exception:
            creator_org_id = None

        if role == 'superadmin':
            raw_org = data.get('org_id') if 'org_id' in data else data.get('orgId')
            raw_org = None if raw_org is None else str(raw_org).strip()
            org_id = raw_org or (str(creator_org_id) if creator_org_id else None)
        else:
            org_id = str(creator_org_id) if creator_org_id else None
            if not org_id:
                return jsonify({'error': 'Your organization is not set. Ask a SuperAdmin to assign your org.'}), 403

        profile_row = {
            'user_id': uid,
            'role': ('superadmin' if requested_role == 'superadmin' else ('admin' if as_admin else 'user')),
            'email': getattr(resp.user, 'email', None) or email,
            'display_name': display_name,
            'org_id': org_id,
        }
        try:
            sb.table('profiles').upsert(profile_row, on_conflict='user_id').execute()
        except Exception as e:
            # Backward-compatible: org_id column may not exist yet.
            if 'org_id' in str(e).lower():
                profile_row.pop('org_id', None)
                sb.table('profiles').upsert(profile_row, on_conflict='user_id').execute()
            else:
                raise
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


@app.route('/api/org/me', methods=['GET'])
@require_auth
def get_my_org(user_id, role):
    """Return current user's profile role + org theme (for client-side theming)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    profile = get_profile(sb, user_id) or {}
    org_id = profile.get('org_id')
    org = None
    if org_id:
        try:
            r = sb.table('organizations').select('id, name, accent_color, created_at, updated_at').eq('id', org_id).limit(1).execute()
            if r.data and len(r.data) > 0:
                org = dict(r.data[0])
                for k in ('created_at', 'updated_at'):
                    if k in org and org[k]:
                        org[k] = str(org[k])
        except Exception as e:
            msg = str(e)
            if 'organizations' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
                return jsonify({'error': 'organizations table not found. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503

    out_profile = {
        'user_id': str(profile.get('user_id') or user_id),
        'role': str(profile.get('role') or role or 'user'),
        'org_id': str(org_id) if org_id else None,
        'display_name': profile.get('display_name'),
        'email': profile.get('email'),
    }
    return jsonify({'profile': out_profile, 'org': org})


# ----- Organizations (superadmin only) -----
@app.route('/api/orgs', methods=['GET'])
@require_superadmin
def list_orgs(user_id, role):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        r = sb.table('organizations').select('id, name, accent_color, created_at, updated_at').order('created_at', desc=False).execute()
        out = []
        for row in (r.data or []):
            o = dict(row)
            for k in ('created_at', 'updated_at'):
                if k in o and o[k]:
                    o[k] = str(o[k])
            out.append(o)
        return jsonify(out)
    except Exception as e:
        msg = str(e)
        if 'organizations' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'organizations table not found. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500


@app.route('/api/orgs', methods=['POST'])
@require_superadmin
def create_org(user_id, role):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    accent = str(data.get('accent_color') or '').strip() or '#c9a962'
    if not name:
        return jsonify({'error': 'name is required'}), 400
    if len(name) > 120:
        return jsonify({'error': 'name must be 120 characters or fewer'}), 400
    if accent and (not accent.startswith('#') or len(accent) not in (4, 7)):
        return jsonify({'error': 'accent_color must be a hex value like #c9a962'}), 400
    now = datetime.utcnow().isoformat()
    try:
        r = sb.table('organizations').insert({
            'name': name,
            'accent_color': accent,
            'created_at': now,
            'updated_at': now,
        }).execute()
        row = (r.data or [None])[0]
        return jsonify({'success': True, 'org': row}), 201
    except Exception as e:
        msg = str(e)
        if 'organizations' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'organizations table not found. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503
        if 'duplicate' in msg.lower() or 'already exists' in msg.lower() or 'unique' in msg.lower():
            return jsonify({'error': 'Organization name already exists'}), 409
        return jsonify({'error': msg}), 500


@app.route('/api/orgs/<org_id>', methods=['PATCH', 'PUT'])
@require_superadmin
def update_org(user_id, role, org_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    data = request.get_json(silent=True) or {}
    upd = {}
    if 'name' in data:
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'name cannot be empty'}), 400
        if len(name) > 120:
            return jsonify({'error': 'name must be 120 characters or fewer'}), 400
        upd['name'] = name
    if 'accent_color' in data:
        accent = str(data.get('accent_color') or '').strip()
        if accent and (not accent.startswith('#') or len(accent) not in (4, 7)):
            return jsonify({'error': 'accent_color must be a hex value like #c9a962'}), 400
        upd['accent_color'] = accent or '#c9a962'
    if not upd:
        return jsonify({'error': 'No fields to update'}), 400
    upd['updated_at'] = datetime.utcnow().isoformat()
    try:
        r = sb.table('organizations').update(upd).eq('id', org_id).execute()
        row = (r.data or [None])[0] if hasattr(r, 'data') else None
        return jsonify({'success': True, 'org': row or {**upd, 'id': org_id}})
    except Exception as e:
        msg = str(e)
        if 'organizations' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'organizations table not found. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500


# ----- Superadmin: assign org/role to users -----
@app.route('/api/profiles/<target_user_id>', methods=['PATCH'])
@require_auth
def superadmin_update_profile(user_id, role, target_user_id):
    if role != 'superadmin':
        return jsonify({'error': 'Forbidden'}), 403
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    data = request.get_json(silent=True) or {}
    upd = {}

    if 'role' in data:
        new_role = str(data.get('role') or '').strip().lower()
        if new_role not in ('superadmin', 'admin', 'user'):
            return jsonify({'error': 'role must be superadmin, admin, or user'}), 400
        upd['role'] = new_role

    org_id = None
    if 'org_id' in data or 'orgId' in data:
        raw_org = data.get('org_id') if 'org_id' in data else data.get('orgId')
        raw_org = None if raw_org is None else str(raw_org).strip()
        org_id = raw_org or None
        upd['org_id'] = org_id

    if not upd:
        return jsonify({'error': 'No fields to update'}), 400

    upd['updated_at'] = datetime.utcnow().isoformat()
    try:
        sb.table('profiles').update(upd).eq('user_id', target_user_id).execute()
    except Exception as e:
        msg = str(e)
        if 'profiles' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'profiles table not found'}), 503
        if 'org_id' in msg.lower() and ('does not exist' in msg.lower() or 'column' in msg.lower()):
            return jsonify({'error': 'org_id column missing. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503
        if 'organizations' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'organizations table not found. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500

    # Keep panoramas org_id consistent with the user's org_id when moved.
    # Note: org_id may be explicitly set to null to "unassign" a user; treat that as a move too.
    if 'org_id' in upd:
        new_org_id = upd.get('org_id')

        def _batches(values, size=200):
            values = list(values or [])
            for i in range(0, len(values), size):
                yield values[i:i + size]

        try:
            sb.table('panoramas').update({'org_id': new_org_id, 'updated_at': datetime.utcnow().isoformat()}).eq('user_id', target_user_id).execute()
        except Exception:
            pass

        # When moving users/orgs, prune any panorama_access rows that would cross org boundaries.
        # This avoids stale cross-org access if a user is moved after previously being shared panoramas.
        try:
            # 1) Revoke this user's access to panoramas outside the new org (or all if org cleared).
            shared = sb.table('panorama_access').select('panorama_id').eq('user_id', target_user_id).execute()
            shared_ids = [row.get('panorama_id') for row in (shared.data or []) if row.get('panorama_id') is not None]
            bad_shared_ids = []
            if shared_ids:
                if new_org_id:
                    panos = []
                    for batch in _batches(shared_ids):
                        r = sb.table('panoramas').select('id, org_id').in_('id', batch).execute()
                        panos.extend(r.data or [])
                    for p in panos:
                        pid = p.get('id')
                        if pid is None:
                            continue
                        if str(p.get('org_id') or '') != str(new_org_id):
                            bad_shared_ids.append(pid)
                else:
                    bad_shared_ids = list(shared_ids)
            for batch in _batches(bad_shared_ids):
                sb.table('panorama_access').delete().eq('user_id', target_user_id).in_('panorama_id', batch).execute()
        except Exception:
            pass

        try:
            # 2) Revoke other users' access to panoramas owned by this user if they are outside the new org
            # (or revoke all shares if org cleared).
            owned = sb.table('panoramas').select('id').eq('user_id', target_user_id).execute()
            owned_ids = [row.get('id') for row in (owned.data or []) if row.get('id') is not None]
            if owned_ids:
                if not new_org_id:
                    for batch in _batches(owned_ids):
                        sb.table('panorama_access').delete().in_('panorama_id', batch).execute()
                else:
                    shared_user_ids = set()
                    for batch in _batches(owned_ids):
                        r = sb.table('panorama_access').select('user_id').in_('panorama_id', batch).execute()
                        for row in (r.data or []):
                            uid = row.get('user_id')
                            if uid:
                                shared_user_ids.add(str(uid))
                    if shared_user_ids:
                        profs = []
                        for batch in _batches(list(shared_user_ids)):
                            r = sb.table('profiles').select('user_id, org_id').in_('user_id', batch).execute()
                            profs.extend(r.data or [])
                        user_to_org = {str(row.get('user_id')): row.get('org_id') for row in profs if row.get('user_id')}
                        outside_users = [uid for uid in shared_user_ids if str(user_to_org.get(uid) or '') != str(new_org_id)]
                        for outside_uid in outside_users:
                            for batch in _batches(owned_ids):
                                sb.table('panorama_access').delete().eq('user_id', outside_uid).in_('panorama_id', batch).execute()
        except Exception:
            pass

    return jsonify({'success': True})


# ----- List users (org-scoped) for granting access -----
@app.route('/api/profiles', methods=['GET'])
@require_auth
def list_profiles(user_id, role):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    org_filter = (request.args.get('org_id') or request.args.get('orgId') or '').strip() or None
    caller_profile = get_profile(sb, user_id) or {}
    caller_org_id = caller_profile.get('org_id')
    try:
        # Scope non-superadmins to their org; superadmins can list all or filter by org_id.
        query = sb.table('profiles').select('user_id, role, display_name, email, created_at, org_id')
        if role != 'superadmin':
            if not caller_org_id:
                return jsonify({'error': 'Your organization is not set. Ask a SuperAdmin to assign your org.'}), 403
            query = query.eq('org_id', caller_org_id)
        elif org_filter:
            query = query.eq('org_id', org_filter)
        r = query.order('created_at', desc=True).execute()
        out = []
        for row in (r.data or []):
            o = dict(row)
            o['user_id'] = str(o['user_id'])
            if o.get('created_at'):
                o['created_at'] = str(o['created_at'])
            if 'org_id' in o and o.get('org_id'):
                o['org_id'] = str(o['org_id'])
            out.append(o)
        return jsonify(out)
    except Exception as e:
        msg = str(e)
        if 'org_id' in msg.lower() and ('does not exist' in msg.lower() or 'column' in msg.lower()):
            return jsonify({'error': 'org_id column missing. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500


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
    if access_type != 'owner' and role not in ('admin', 'superadmin'):
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
    if access_type != 'owner' and role not in ('admin', 'superadmin'):
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

    # Enforce org boundaries: org admins/owners can only grant access within their org.
    try:
        panorama_org_id = panorama.get('org_id') if isinstance(panorama, dict) else None
    except Exception:
        panorama_org_id = None

    if panorama_org_id and role != 'superadmin':
        try:
            caller = sb.table('profiles').select('org_id').eq('user_id', user_id).limit(1).execute()
            target = sb.table('profiles').select('org_id').eq('user_id', target_user_id).limit(1).execute()
            caller_org = (caller.data or [{}])[0].get('org_id') if hasattr(caller, 'data') else None
            target_org = (target.data or [{}])[0].get('org_id') if hasattr(target, 'data') else None
        except Exception as e:
            msg = str(e).lower()
            if 'org_id' in msg and ('does not exist' in msg or 'column' in msg):
                return jsonify({'error': 'org_id column missing. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503
            caller_org = None
            target_org = None

        if not caller_org or not target_org:
            return jsonify({'error': 'User organization is not set. Ask a SuperAdmin to assign orgs.'}), 403
        if str(caller_org) != str(panorama_org_id) or str(target_org) != str(panorama_org_id):
            return jsonify({'error': 'You can only grant access to users in your organization'}), 403

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
    if access_type != 'owner' and role not in ('admin', 'superadmin'):
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
    if new_role not in ('superadmin', 'admin', 'user'):
        return jsonify({'error': 'role must be superadmin, admin, or user'}), 400
    if new_role == 'superadmin' and role != 'superadmin':
        return jsonify({'error': 'Only superadmins can grant superadmin role'}), 403
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        if role != 'superadmin':
            # Org admins can only manage roles inside their org.
            caller = get_profile(sb, admin_id) or {}
            caller_org = caller.get('org_id')
            target = get_profile(sb, user_id) or {}
            target_org = target.get('org_id')
            if not caller_org or not target_org or str(caller_org) != str(target_org):
                return jsonify({'error': 'You can only manage users in your organization'}), 403
        sb.table('profiles').update({'role': new_role, 'updated_at': datetime.utcnow().isoformat()}).eq('user_id', user_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        msg = str(e)
        if 'org_id' in msg.lower() and ('does not exist' in msg.lower() or 'column' in msg.lower()):
            return jsonify({'error': 'org_id column missing. Run supabase_migration_orgs.sql in Supabase SQL Editor.'}), 503
        if 'role' in msg.lower() and 'check' in msg.lower():
            return jsonify({'error': 'profiles.role constraint does not allow this value. Run supabase_migration_orgs.sql.'}), 503
        return jsonify({'error': msg}), 500


def _crm_panorama_ids(sb, user_id):
    """
    Panoramas this user can manage for CRM purposes.
    Owner panoramas + panoramas where user has 'client' access (not 'viewer'),
    including folder-level client shares.
    """
    ids = set()
    try:
        owned = sb.table('panoramas').select('id').eq('user_id', user_id).execute()
        for row in (owned.data or []):
            try:
                ids.add(int(row.get('id')))
            except Exception:
                pass
    except Exception:
        pass
    try:
        shared = sb.table('panorama_access').select('panorama_id').eq('user_id', user_id).eq('access_type', 'client').execute()
        for row in (shared.data or []):
            try:
                ids.add(int(row.get('panorama_id')))
            except Exception:
                pass
    except Exception:
        pass
    try:
        ws = (
            sb.table('workspace_access')
            .select('workspace_id')
            .eq('user_id', user_id)
            .eq('access_type', 'client')
            .execute()
        )
        workspace_ids = []
        for row in (ws.data or []):
            wsid = row.get('workspace_id')
            if wsid:
                workspace_ids.append(str(wsid))
        workspace_ids = list(dict.fromkeys(workspace_ids))
    except Exception:
        workspace_ids = []

    if workspace_ids:
        chunk_size = 100
        for i in range(0, len(workspace_ids), chunk_size):
            chunk = workspace_ids[i:i + chunk_size]
            try:
                panos = sb.table('panoramas').select('id').in_('workspace_id', chunk).execute()
            except Exception:
                continue
            for row in (panos.data or []):
                try:
                    ids.add(int(row.get('id')))
                except Exception:
                    pass
    return sorted(ids)


@app.route('/api/buy-interests', methods=['POST'])
@require_auth
def create_buy_interest(user_id, role):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    data = request.get_json(silent=True) or {}
    panorama_id = data.get('panorama_id')
    try:
        panorama_id = int(panorama_id)
    except Exception:
        panorama_id = None

    customer_name = str(data.get('customer_name') or data.get('name') or '').strip()
    customer_email = str(data.get('customer_email') or data.get('email') or '').strip()
    customer_phone = str(data.get('customer_phone') or data.get('phone') or '').strip()
    category = str(data.get('category') or '').strip()

    items = data.get('items') or data.get('plots') or []
    if not isinstance(items, list):
        items = []

    if not panorama_id:
        return jsonify({'error': 'panorama_id is required'}), 400
    if not customer_name:
        return jsonify({'error': 'Name is required'}), 400
    if not customer_email or '@' not in customer_email:
        return jsonify({'error': 'Valid email is required'}), 400
    if not customer_phone:
        return jsonify({'error': 'Contact number is required'}), 400
    if not category:
        return jsonify({'error': 'Category is required'}), 400

    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404

    plot_ids = []
    for item in items:
        if isinstance(item, (int, float, str)):
            raw = item
        elif isinstance(item, dict):
            raw = item.get('plot_id') if 'plot_id' in item else item.get('id')
        else:
            raw = None
        if raw is None or str(raw).strip() == '':
            continue
        try:
            plot_ids.append(int(raw))
        except Exception:
            continue
    plot_ids = list(dict.fromkeys(plot_ids))
    if not plot_ids:
        return jsonify({'error': 'At least one plot is required'}), 400

    plots_snapshot = []
    try:
        r = (
            sb.table('plots')
            .select('id, panorama_id, name, area, price, status')
            .in_('id', plot_ids)
            .eq('panorama_id', panorama_id)
            .execute()
        )
        found = {int(row.get('id')): row for row in (r.data or []) if row and row.get('id') is not None}
        for pid in plot_ids:
            row = found.get(int(pid))
            if not row:
                continue
            plots_snapshot.append({
                'plot_id': int(row.get('id')),
                'name': row.get('name') or '',
                'area': row.get('area') or '',
                'price': row.get('price') or '',
                'status': row.get('status') or '',
            })
    except Exception as e:
        msg = str(e)
        if 'buy_interests' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'buy_interests table not found. Run supabase_migration_buy_interests.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500

    if not plots_snapshot:
        return jsonify({'error': 'No valid plots found for this panorama'}), 400

    now = datetime.utcnow().isoformat()
    insert_row = {
        'panorama_id': panorama_id,
        'submitted_by': user_id,
        'customer_name': customer_name,
        'customer_email': customer_email,
        'customer_phone': customer_phone,
        'category': category,
        'plots': plots_snapshot,
        'status': 'new',
        'notes': '',
        'created_at': now,
        'updated_at': now,
    }

    try:
        r = sb.table('buy_interests').insert(insert_row).execute()
        row = (r.data or [None])[0] if hasattr(r, 'data') else None
        return jsonify({'success': True, 'buy_interest': row or insert_row}), 201
    except Exception as e:
        msg = str(e)
        if 'buy_interests' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'buy_interests table not found. Run supabase_migration_buy_interests.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500


@app.route('/api/buy-interests', methods=['GET'])
@require_auth
def list_buy_interests(user_id, role):
    """CRM list: interests for panoramas where user is owner or has client access."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    panorama_ids = _crm_panorama_ids(sb, user_id)
    if not panorama_ids:
        return jsonify([])

    panorama_id = request.args.get('panorama_id')
    status = (request.args.get('status') or '').strip().lower()
    q = (request.args.get('q') or '').strip()
    try:
        if panorama_id is not None and str(panorama_id).strip() != '':
            panorama_id = int(panorama_id)
        else:
            panorama_id = None
    except Exception:
        panorama_id = None

    try:
        query = sb.table('buy_interests').select('*').in_('panorama_id', panorama_ids)
        if panorama_id and panorama_id in panorama_ids:
            query = query.eq('panorama_id', panorama_id)
        if status in ('new', 'contacted', 'qualified', 'won', 'lost'):
            query = query.eq('status', status)
        if q:
            token = q.replace('%', '').replace('(', '').replace(')', '').replace(',', '')
            query = query.or_(
                f"customer_name.ilike.%{token}%,customer_email.ilike.%{token}%,customer_phone.ilike.%{token}%"
            )
        r = query.order('created_at', desc=True).limit(500).execute()
        out = []
        for row in (r.data or []):
            o = dict(row)
            for k in ('created_at', 'updated_at'):
                if k in o and o[k]:
                    o[k] = str(o[k])
            if 'submitted_by' in o and o['submitted_by']:
                o['submitted_by'] = str(o['submitted_by'])
            out.append(o)
        return jsonify(out)
    except Exception as e:
        msg = str(e)
        if 'buy_interests' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'buy_interests table not found. Run supabase_migration_buy_interests.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500


@app.route('/api/buy-interests/<interest_id>', methods=['PUT', 'PATCH'])
@require_auth
def update_buy_interest(user_id, role, interest_id):
    """CRM update: status/notes (only for owner or client panoramas)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503

    panorama_ids = _crm_panorama_ids(sb, user_id)
    if not panorama_ids:
        return jsonify({'error': 'Forbidden'}), 403

    try:
        r = sb.table('buy_interests').select('id, panorama_id').eq('id', interest_id).limit(1).execute()
        if not r.data or len(r.data) == 0:
            return jsonify({'error': 'Not found'}), 404
        panorama_id = r.data[0].get('panorama_id')
        try:
            panorama_id = int(panorama_id)
        except Exception:
            panorama_id = None
        if not panorama_id or panorama_id not in panorama_ids:
            return jsonify({'error': 'Forbidden'}), 403
    except Exception as e:
        msg = str(e)
        if 'buy_interests' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'buy_interests table not found. Run supabase_migration_buy_interests.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500

    data = request.get_json(silent=True) or {}
    upd = {}
    if 'status' in data:
        status = str(data.get('status') or '').strip().lower()
        if status not in ('new', 'contacted', 'qualified', 'won', 'lost'):
            return jsonify({'error': 'Invalid status'}), 400
        upd['status'] = status
    if 'notes' in data:
        upd['notes'] = str(data.get('notes') or '').strip()

    if not upd:
        return jsonify({'error': 'Nothing to update'}), 400

    upd['updated_at'] = datetime.utcnow().isoformat()
    try:
        sb.table('buy_interests').update(upd).eq('id', interest_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        msg = str(e)
        if 'buy_interests' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
            return jsonify({'error': 'buy_interests table not found. Run supabase_migration_buy_interests.sql in Supabase SQL Editor.'}), 503
        return jsonify({'error': msg}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug, host='0.0.0.0', port=port)
