"""
Real Estate Panorama Plot Marker - Flask Application
Uses Supabase for Auth + DB. All data is user-scoped; admin can grant client/viewer access.
"""
import os
import json
import base64
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

from db import (
    get_supabase,
    get_current_user,
    require_auth,
    require_admin,
    get_panorama_with_access,
    get_panorama_by_id,
    list_panoramas_for_user,
    can_edit_plots,
    can_add_markers,
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

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
IMAGE_CONTENT_TYPES = {
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'webp': 'image/webp',
}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('templates', exist_ok=True)
os.makedirs('static', exist_ok=True)


def auth_ctx():
    return {'supabase_url': SUPABASE_URL, 'supabase_anon_key': SUPABASE_ANON_KEY}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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
    """Serve panorama image from DB (base64) when present, else from disk."""
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
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    try:
        from PIL import Image
        with Image.open(filepath) as img:
            width, height = img.size
    except Exception:
        width, height = 0, 0

    # Store panorama image as base64 in DB (for serving from Postgres)
    image_b64 = None
    image_content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        if len(raw) <= 20 * 1024 * 1024:  # 20MB max for panorama
            image_b64 = base64.b64encode(raw).decode('ascii')
    except Exception:
        pass

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
    }
    if image_b64 is not None:
        insert_row['image_data'] = image_b64
        insert_row['image_content_type'] = image_content_type
    try:
        r = sb.table('panoramas').insert(insert_row).execute()
        if not r.data or len(r.data) == 0:
            return jsonify({'error': 'Insert failed'}), 500
        row = r.data[0]
        panorama_id = row['id']
    except Exception as e:
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
        r = sb.table('plots').select('*').eq('panorama_id', panorama_id).order('created_at').execute()
        plots = []
        for row in (r.data or []):
            p = dict(row)
            if isinstance(p.get('points'), str):
                try:
                    p['points'] = json.loads(p['points'])
                except Exception:
                    p['points'] = []
            # Exclude blob from list; frontend uses GET /api/plots/<id>/image when has_image
            p['has_image'] = bool(p.pop('image_data', None))
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
MAX_PLOT_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB


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
    return Response(data, mimetype=content_type)


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
        image_b64 = base64.b64encode(raw).decode('ascii')
        content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
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
    return jsonify({'success': True, 'content_type': content_type})


# ----- Markers (point markers; only owner/admin add; clients can edit details only) -----
@app.route('/api/panoramas/<int:panorama_id>/markers', methods=['GET'])
@require_auth
def get_markers(user_id, role, panorama_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, _ = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    try:
        r = sb.table('markers').select('*').eq('panorama_id', panorama_id).order('created_at').execute()
        markers = []
        for row in (r.data or []):
            m = dict(row)
            if isinstance(m.get('position'), str):
                try:
                    m['position'] = json.loads(m['position'])
                except Exception:
                    m['position'] = {'x': 0, 'y': 0}
            markers.append(m)
        return jsonify(markers)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/panoramas/<int:panorama_id>/markers', methods=['POST'])
@require_auth
def create_marker(user_id, role, panorama_id):
    data = request.get_json()
    if not data or not data.get('position'):
        return jsonify({'error': 'Position is required'}), 400
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    if not can_add_markers(access_type, role):
        return jsonify({'error': 'Only owner or admin can add markers'}), 403
    pos = data['position']
    if not isinstance(pos, dict) or 'x' not in pos or 'y' not in pos:
        return jsonify({'error': 'Position must be {x, y}'}), 400
    marker_type = (data.get('marker_type') or 'pin').lower()
    if marker_type not in ('pin', 'flag', 'star', 'info', 'heart', 'warning', 'check'):
        marker_type = 'pin'
    try:
        r = sb.table('markers').insert({
            'panorama_id': panorama_id,
            'marker_type': marker_type,
            'name': data.get('name', ''),
            'description': data.get('description', ''),
            'position': pos,
            'area': data.get('area', ''),
            'price': data.get('price', ''),
            'status': data.get('status', 'available'),
            'color': data.get('color', 'emerald'),
            'media_photo': data.get('media_photo', ''),
            'media_video': data.get('media_video', ''),
        }).execute()
        if not r.data or len(r.data) == 0:
            return jsonify({'error': 'Insert failed'}), 500
        row = r.data[0]
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
        return jsonify({'id': row['id'], 'panorama_id': panorama_id, **data}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/markers/<int:marker_id>', methods=['PUT'])
@require_auth
def update_marker(user_id, role, marker_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Body required'}), 400
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        mr = sb.table('markers').select('panorama_id').eq('id', marker_id).limit(1).execute()
        if not mr.data or len(mr.data) == 0:
            return jsonify({'error': 'Marker not found'}), 404
        panorama_id = mr.data[0]['panorama_id']
    except Exception:
        return jsonify({'error': 'Not found'}), 404
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Forbidden'}), 403
    updating_position = 'position' in data and data['position'] is not None
    if updating_position and not can_add_markers(access_type, role):
        return jsonify({'error': 'Only owner or admin can change marker position'}), 403
    if not updating_position and not can_edit_plots(access_type):
        return jsonify({'error': 'You cannot edit this marker'}), 403
    try:
        upd = {'updated_at': datetime.utcnow().isoformat()}
        if 'name' in data:
            upd['name'] = data.get('name', '')
        if 'description' in data:
            upd['description'] = data.get('description', '')
        if 'area' in data:
            upd['area'] = data.get('area', '')
        if 'price' in data:
            upd['price'] = data.get('price', '')
        if 'status' in data:
            upd['status'] = data.get('status', 'available')
        if 'color' in data:
            upd['color'] = data.get('color', 'emerald')
        if 'media_photo' in data:
            upd['media_photo'] = data.get('media_photo', '')
        if 'media_video' in data:
            upd['media_video'] = data.get('media_video', '')
        if 'marker_type' in data:
            mt = (data.get('marker_type') or 'pin').lower()
            upd['marker_type'] = mt if mt in ('pin', 'flag', 'star', 'info', 'heart', 'warning', 'check') else 'pin'
        if updating_position:
            pos = data['position']
            if isinstance(pos, dict) and 'x' in pos and 'y' in pos:
                upd['position'] = pos
        sb.table('markers').update(upd).eq('id', marker_id).execute()
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/markers/<int:marker_id>', methods=['DELETE'])
@require_auth
def delete_marker(user_id, role, marker_id):
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    try:
        mr = sb.table('markers').select('panorama_id').eq('id', marker_id).limit(1).execute()
        if not mr.data or len(mr.data) == 0:
            return jsonify({'error': 'Marker not found'}), 404
        panorama_id = mr.data[0]['panorama_id']
    except Exception:
        return jsonify({'error': 'Not found'}), 404
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama or not can_add_markers(access_type, role):
        return jsonify({'error': 'Only owner or admin can delete markers'}), 403
    try:
        sb.table('markers').delete().eq('id', marker_id).execute()
        sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/panoramas/<int:panorama_id>/access-info', methods=['GET'])
@require_auth
def panorama_access_info(user_id, role, panorama_id):
    """Return current user's access_type and can_add_markers for the panorama (for UI)."""
    sb = get_supabase()
    if not sb:
        return jsonify({'error': 'Database not configured'}), 503
    panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
    if not panorama:
        return jsonify({'error': 'Panorama not found'}), 404
    return jsonify({
        'access_type': access_type,
        'can_add_markers': can_add_markers(access_type, role),
    })


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
