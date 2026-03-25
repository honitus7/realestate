"""
Register all Flask routes. Uses app.core and app.services only.
"""
import json
import os
import re
import time
import uuid
from datetime import datetime

import requests as _requests

from flask import request, jsonify, render_template, redirect, Response, send_from_directory, current_app, make_response
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from itsdangerous import BadSignature, SignatureExpired

from app import config as app_config
from app.core.database import get_supabase
from app.core.auth import get_profile, require_auth, require_admin, require_superadmin
from app.core.serializers import (
    page_access_serializer,
    issue_page_access_token as _issue_page_access_token_impl,
    panorama_upload_serializer,
    plot_upload_serializer,
    marker_upload_serializer,
)
from app.services.panorama_service import (
    get_panorama_with_access,
    list_panoramas_for_user,
    get_panorama_by_id,
    get_mobile_panorama_by_parent_id,
    can_edit_plots,
    can_delete_panorama,
)
from app.services.workspace_service import (
    get_workspace_by_id,
    serialize_workspace_row,
    clear_workspace_main_for_panorama,
    can_manage_workspace,
    list_workspaces as ws_list_workspaces,
    create_workspace as ws_create_workspace,
    update_workspace as ws_update_workspace,
    delete_workspace as ws_delete_workspace,
    get_workspace_schema_error_response,
    is_workspace_schema_missing,
    project_fields_from_payload,
    update_customer_config as ws_update_customer_config,
    get_customer_config as ws_get_customer_config,
    ensure_panorama_added_to_workspace_config as ws_ensure_panorama_added_to_workspace_config,
    get_workspace_share_endpoint as ws_get_workspace_share_endpoint,
    is_workspace_share_endpoint_available as ws_is_workspace_share_endpoint_available,
    update_workspace_share_endpoint as ws_update_workspace_share_endpoint,
    get_workspace_id_by_share_endpoint as ws_get_workspace_id_by_share_endpoint,
    normalize_workspace_share_endpoint as ws_normalize_workspace_share_endpoint,
)
from app.services.storage_service import (
    use_s3,
    get_s3_client,
    panorama_object_key,
    get_panorama_s3_url,
    get_panorama_thumb_s3_url,
    generate_panorama_thumb_bytes,
    upload_panorama_thumb_to_s3,
    MAX_PANORAMA_THUMB_READ_BYTES,
    get_panorama_optimized_s3_url,
    generate_panorama_optimized_bytes,
    upload_panorama_optimized_to_s3,
    MAX_PANORAMA_OPTIMIZED_READ_BYTES,
    plot_object_key,
    get_plot_s3_url,
    delete_plot_from_s3,
    marker_object_key,
    marker_thumb_object_key,
    get_marker_s3_url,
    delete_marker_from_s3,
    voiceover_object_key,
    get_voiceover_s3_url,
    delete_voiceover_from_s3,
    delete_panorama_from_s3,
    upload_panorama_to_s3,
    read_uploaded_file_bytes,
    compress_marker_image,
    probe_image_dimensions,
    upload_daynight_to_s3,
    get_daynight_s3_url,
    delete_daynight_from_s3,
    stitch_images_horizontally,
    MAX_DAYNIGHT_IMAGE_READ_BYTES,
    convert_floorplan_to_webp_lossless,
    upload_floorplan_to_s3,
    get_floorplan_s3_url,
    delete_floorplan_from_s3,
    MAX_FLOORPLAN_READ_BYTES,
    compress_building_map_image,
    upload_building_map_to_s3,
    get_building_map_s3_url,
    delete_building_map_from_s3,
    MAX_BUILDING_MAP_READ_BYTES,
    compress_gallery_image,
    upload_gallery_to_s3,
    get_gallery_s3_url,
    delete_gallery_from_s3,
    MAX_GALLERY_READ_BYTES,
    ALLOWED_GALLERY_IMAGE_EXT,
    ALLOWED_GALLERY_VIDEO_EXT,
)
from app.services.org_service import get_org_name_and_slug_for_panorama, slugify_org_name
from app.services.plot_service import fetch_plots, create_plot as plot_create, get_plot_panorama_id, delete_plot as plot_delete, update_plot as plot_update, update_plot_label_position as plot_update_label_position
from app.services.daynight_service import (
    create_daynight_project as dn_create,
    get_daynight_project as dn_get,
    get_daynight_project_by_token as dn_get_by_token,
    list_daynight_projects as dn_list,
    update_daynight_project as dn_update,
    delete_daynight_project as dn_delete,
)
from app.services.floorplan_service import (
    create_catalogue as fp_create_catalogue,
    get_catalogue as fp_get_catalogue,
    get_catalogue_by_token as fp_get_catalogue_by_token,
    list_catalogues as fp_list_catalogues,
    update_catalogue as fp_update_catalogue,
    delete_catalogue as fp_delete_catalogue,
    list_items as fp_list_items,
    get_item as fp_get_item,
    create_item as fp_create_item,
    update_item as fp_update_item,
    delete_item as fp_delete_item,
    reorder_items as fp_reorder_items,
    get_next_sort_order as fp_next_sort_order,
)
from app.services.building_map_service import (
    create_building_map as bm_create,
    get_building_map as bm_get,
    get_building_map_by_token as bm_get_by_token,
    list_building_maps as bm_list,
    update_building_map as bm_update,
    delete_building_map as bm_delete,
    list_images as bm_list_images,
    get_image as bm_get_image,
    create_image as bm_create_image,
    update_image as bm_update_image,
    delete_image as bm_delete_image,
    get_next_image_sort_order as bm_next_image_sort,
    list_zones as bm_list_zones,
    list_zones_for_image as bm_list_zones_for_image,
    get_zone as bm_get_zone,
    create_zone as bm_create_zone,
    batch_create_zones as bm_batch_create_zones,
    update_zone as bm_update_zone,
    delete_zone as bm_delete_zone,
    get_floor_numbers as bm_get_floors,
)
from app.services.project_plan_service import (
    create_project_plan as pp_create,
    get_project_plan as pp_get,
    get_project_plan_by_token as pp_get_by_token,
    list_project_plans as pp_list,
    update_project_plan as pp_update,
    delete_project_plan as pp_delete,
    list_plan_maps as pp_list_maps,
    add_map_to_plan as pp_add_map,
    remove_map_from_plan as pp_remove_map,
    get_next_plan_map_sort_order as pp_next_sort,
    reorder_plan_maps as pp_reorder,
)
from app.services.gallery_service import (
    create_gallery as gal_create,
    get_gallery as gal_get,
    get_gallery_by_token as gal_get_by_token,
    list_galleries as gal_list,
    update_gallery as gal_update,
    delete_gallery as gal_delete,
    list_items as gal_list_items,
    get_item as gal_get_item,
    create_item as gal_create_item,
    update_item as gal_update_item,
    delete_item as gal_delete_item,
    reorder_items as gal_reorder_items,
    get_next_sort_order as gal_next_sort_order,
)
from app.config import (
    ALLOWED_EXTENSIONS,
    ALLOWED_AUDIO_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    IMAGE_CONTENT_TYPES,
    AUDIO_CONTENT_TYPES,
    VIDEO_CONTENT_TYPES,
    CONTENT_TYPE_TO_EXT,
    PAGE_ACCESS_TOKEN_TTL,
    SUPABASE_S3_BUCKET,
    SUPABASE_S3_SIGNED_URL_TTL,
    SUPABASE_S3_UPLOAD_URL_TTL,
    MAX_DAYNIGHT_VIDEO_BYTES,
)

MAX_PLOT_IMAGE_BYTES = int(os.environ.get('MAX_PLOT_IMAGE_BYTES', str(25 * 1024 * 1024)))
MAX_MARKER_IMAGE_BYTES = int(os.environ.get('MAX_MARKER_IMAGE_BYTES', str(5 * 1024 * 1024)))
MAX_MARKER_VOICEOVER_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = ALLOWED_EXTENSIONS


def _detect_ext_from_content_type(content_type):
    ext = (content_type or '').lower().strip()
    ext = CONTENT_TYPE_TO_EXT.get(ext, 'jpg')
    return ext if ext in ALLOWED_EXTENSIONS else 'jpg'


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def auth_ctx():
    return {'supabase_url': app_config.SUPABASE_URL, 'supabase_anon_key': app_config.SUPABASE_ANON_KEY}


def _ws_error_response():
    body, status = get_workspace_schema_error_response()
    return jsonify(body), status


def _workspace_share_payload(workspace_id, custom_endpoint=None):
    base = (request.url_root or '').rstrip('/')
    default_url = f"{base}/customer/project/{workspace_id}"
    normalized = ws_normalize_workspace_share_endpoint(custom_endpoint)
    custom = normalized or None
    custom_url = f"{base}/panoview/{custom}" if custom else None
    return {
        'workspace_id': str(workspace_id),
        'default_url': default_url,
        'custom_endpoint': custom,
        'custom_url': custom_url,
        'effective_url': custom_url or default_url,
    }


def register_routes(app):
    secret_key = app.secret_key or app_config.SECRET_KEY

    def _issue_page_access_token(user_id, panorama_id, mode):
        return _issue_page_access_token_impl(secret_key, user_id, panorama_id, mode)

    def _load_panorama_for_page_mode(panorama_id, mode):
        sb = get_supabase()
        if not sb:
            return None, None, ("Database not configured", 503)
        token = str(request.args.get('pt') or '').strip()
        if not token:
            return None, None, ("Forbidden", 403)
        try:
            payload = page_access_serializer(secret_key).loads(token, max_age=int(PAGE_ACCESS_TOKEN_TTL))
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

    def _crm_panorama_ids(sb, user_id, role='user'):
        normalized_role = str(role or 'user').strip().lower()
        if normalized_role in ('admin', 'superadmin'):
            try:
                if normalized_role == 'superadmin':
                    return _fetch_panorama_ids(sb)
                if normalized_role == 'admin':
                    caller = get_profile(sb, user_id) or {}
                    caller_org = caller.get('org_id')
                    if caller_org:
                        return _fetch_panorama_ids(sb, lambda q: q.eq('org_id', caller_org))
            except Exception:
                pass
        # Regular users: see panoramas they created, have client access to, or have lock access to
        ids = set()
        # 1. Panoramas the user owns (created)
        try:
            owned = sb.table('panoramas').select('id').eq('user_id', user_id).execute()
            for row in (owned.data or []):
                try:
                    ids.add(int(row.get('id')))
                except Exception:
                    pass
        except Exception:
            pass
        # 2. Panoramas with client access via panorama_access
        try:
            access_rows = sb.table('panorama_access').select('panorama_id, access_type').eq('user_id', user_id).execute()
            for row in (access_rows.data or []):
                try:
                    at = str(row.get('access_type') or '').lower()
                    if at in ('owner', 'client'):
                        ids.add(int(row.get('panorama_id')))
                except Exception:
                    pass
        except Exception:
            pass
        # 3. Panoramas with client access via workspace_access (folder sharing)
        try:
            ws_rows = sb.table('workspace_access').select('workspace_id, access_type').eq('user_id', user_id).execute()
            ws_ids = []
            for row in (ws_rows.data or []):
                at = str(row.get('access_type') or '').lower()
                if at in ('owner', 'client'):
                    wsid = row.get('workspace_id')
                    if wsid:
                        ws_ids.append(wsid)
            if ws_ids:
                ws_panos = sb.table('panoramas').select('id').in_('workspace_id', ws_ids).execute()
                for row in (ws_panos.data or []):
                    try:
                        ids.add(int(row.get('id')))
                    except Exception:
                        pass
        except Exception:
            pass
        # 4. Panoramas with lock access
        try:
            lock_access = sb.table('plot_lock_access').select('panorama_id').eq('user_id', user_id).execute()
            for row in (lock_access.data or []):
                try:
                    ids.add(int(row.get('panorama_id')))
                except Exception:
                    pass
        except Exception:
            pass
        return sorted(ids)

    def _fetch_panorama_ids(sb, query_builder=None):
        ids = set()
        page_size = 500
        start = 0
        while True:
            q = sb.table('panoramas').select('id')
            if callable(query_builder):
                q = query_builder(q)
            if q is None:
                break
            rows = []
            try:
                r = q.range(start, start + page_size - 1).execute()
                rows = r.data or []
            except Exception:
                if start > 0:
                    break
                r = q.execute()
                rows = r.data or []
            for row in rows:
                try:
                    ids.add(int(row.get('id')))
                except Exception:
                    pass
            if len(rows) < page_size:
                break
            start += page_size
        return sorted(ids)

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(_error):
        if request.path.startswith('/api/'):
            max_bytes = current_app.config.get('MAX_CONTENT_LENGTH') or 0
            max_mb = max(1, int(max_bytes / (1024 * 1024))) if max_bytes else 0
            hint = f' (max {max_mb}MB)' if max_mb else ''
            return jsonify({'error': f'Upload too large{hint}'}), 413
        return _error

    # ----- Public routes -----
    @app.route('/')
    def index():
        return render_template('landing.html')

    @app.route('/login')
    def login_page():
        return render_template('login.html', **auth_ctx())

    @app.route('/auth/callback')
    def auth_callback():
        return render_template('auth_callback.html', **auth_ctx())

    @app.route('/dashboard')
    @app.route('/dashboard/<user_id>')
    def dashboard(user_id=None):
        return render_template('dashboard.html', user_id=user_id, **auth_ctx())

    @app.route('/crm')
    def crm_page():
        from flask import make_response
        resp = make_response(render_template('crm.html', **auth_ctx()))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        return resp

    @app.route('/explore')
    @app.route('/portal')
    def customer_portal():
        return render_template('portal.html', **auth_ctx())

    @app.route('/organizations')
    def organizations_page():
        return render_template('organizations.html', **auth_ctx())

    @app.route('/users')
    def users_page():
        return render_template('add_user.html', **auth_ctx())

    @app.route('/user-management')
    def user_management_page():
        return render_template('user_management.html', **auth_ctx())

    @app.route('/panorama/<int:panorama_id>/access')
    def panorama_access_page(panorama_id):
        return render_template('panorama_access.html', panorama_id=panorama_id, **auth_ctx())

    @app.route('/workspace/<workspace_id>/access')
    def workspace_access_page(workspace_id):
        return render_template('workspace_access.html', workspace_id=workspace_id, **auth_ctx())

    @app.route('/admin/<int:panorama_id>')
    def admin(panorama_id):
        sb, panorama, err = _load_panorama_for_page_mode(panorama_id, 'admin')
        if err:
            return err
        org_name, org_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        mobile_panorama = get_mobile_panorama_by_parent_id(sb, panorama_id)
        return render_template('editor.html', panorama=panorama, mobile_panorama=mobile_panorama, mode='admin', org_name=org_name, org_slug=org_slug, **auth_ctx())

    def load_customer_workspace_panoramas(sb, workspace_id, main_id=None):
        workspace_panoramas = []
        if not workspace_id:
            return workspace_panoramas
        try:
            r = sb.table('panoramas').select('id, name, filename, is_360').eq('workspace_id', workspace_id).order('id').execute()
            rows = list(r.data or [])
            if main_id is not None:
                try:
                    mid = int(main_id)
                except Exception:
                    mid = None
                if mid is not None:
                    def sort_key(p):
                        pid = p.get('id')
                        if pid == mid:
                            return (0, pid or 0)
                        return (1, pid or 0)
                    rows.sort(key=sort_key)
            for p in rows:
                workspace_panoramas.append({
                    'id': p.get('id'),
                    'name': (p.get('name') or '').strip() or ('Panorama #' + str(p.get('id') or '')),
                    'filename': p.get('filename') or '',
                    'is_360': bool(p.get('is_360')),
                })
        except Exception:
            pass
        return workspace_panoramas

    def load_mobile_panorama_map(sb, parent_panorama_ids):
        out = {}
        ids = []
        seen = set()
        for raw in (parent_panorama_ids or []):
            try:
                pid = int(raw)
            except Exception:
                continue
            if pid in seen:
                continue
            seen.add(pid)
            ids.append(pid)
        if not ids:
            return out
        try:
            r = (
                sb.table('mobile_panoramas')
                .select('id, panorama_parent_id, name, filename, original_filename, width, height, is_360')
                .in_('panorama_parent_id', ids)
                .order('id')
                .execute()
            )
            for row in (r.data or []):
                try:
                    parent_id = int(row.get('panorama_parent_id'))
                except Exception:
                    continue
                out[str(parent_id)] = {
                    'id': row.get('id'),
                    'panorama_parent_id': row.get('panorama_parent_id'),
                    'name': row.get('name') or '',
                    'filename': row.get('filename') or '',
                    'original_filename': row.get('original_filename') or '',
                    'width': row.get('width') or 0,
                    'height': row.get('height') or 0,
                    'is_360': bool(row.get('is_360')),
                }
        except Exception:
            pass
        return out

    def build_customer_workspace_context(sb, panorama):
        workspace_id = (panorama or {}).get('workspace_id')
        customer_view_config = {}
        workspace_panoramas = []
        mobile_panorama_map = {}
        if workspace_id:
            try:
                customer_view_config = ws_get_customer_config(sb, workspace_id) or {}
            except Exception:
                pass
            workspace_panoramas = load_customer_workspace_panoramas(
                sb,
                workspace_id,
                (get_workspace_by_id(sb, workspace_id) or {}).get('main_panorama_id')
            )
        parent_ids = [p.get('id') for p in (workspace_panoramas or []) if p and p.get('id') is not None]
        if panorama and panorama.get('id') is not None:
            parent_ids.append(panorama.get('id'))
        mobile_panorama_map = load_mobile_panorama_map(sb, parent_ids)
        return workspace_id, workspace_panoramas, customer_view_config, mobile_panorama_map

    def render_customer_panorama_template(sb, panorama, org_name, canonical_slug, full_view=False, initial_panorama_id=None):
        workspace_id, workspace_panoramas, customer_view_config, mobile_panorama_map = build_customer_workspace_context(sb, panorama)
        resp = make_response(render_template(
            'customer_3d.html',
            panorama=panorama,
            org_name=org_name,
            org_slug=canonical_slug,
            full_view=full_view,
            workspace_panoramas=workspace_panoramas,
            initial_panorama_id=initial_panorama_id,
            workspace_id=workspace_id,
            customer_view_config=customer_view_config,
            mobile_panorama_map=mobile_panorama_map,
            **auth_ctx()
        ))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        return resp

    @app.route('/customer/edit/<workspace_id>')
    def customer_edit_view(workspace_id):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        workspace = get_workspace_by_id(sb, workspace_id)
        if not workspace:
            return "Workspace not found", 404
        main_id = workspace.get('main_panorama_id')
        panorama = None
        if main_id:
            panorama = get_panorama_by_id(sb, main_id)
        if not panorama:
            try:
                r = sb.table('panoramas').select('*').eq('workspace_id', workspace_id).order('id').limit(1).execute()
                if r.data and len(r.data) > 0:
                    panorama = dict(r.data[0])
            except Exception:
                pass
        if not panorama:
            return "No panorama in this workspace", 404
        org_name, canonical_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        workspace_panoramas = load_customer_workspace_panoramas(sb, workspace_id, main_id)
        return render_template(
            'customer_edit.html',
            panorama=panorama,
            org_name=org_name,
            org_slug=canonical_slug,
            workspace_id=workspace_id,
            workspace_name=workspace.get('name', ''),
            workspace_panoramas=workspace_panoramas,
            initial_panorama_id=panorama.get('id'),
            customer_view_config={},
            **auth_ctx()
        )

    @app.route('/customer/<int:panorama_id>')
    def customer(panorama_id):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        panorama = get_panorama_by_id(sb, panorama_id)
        if not panorama:
            return "Panorama not found", 404
        _org_name, org_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        qs = request.query_string.decode('utf-8') if request.query_string else ''
        suffix = ('?' + qs) if qs else ''
        is_360 = bool((panorama or {}).get('is_360'))
        if is_360:
            return redirect(f"/customer/{org_slug}/3d/{panorama_id}{suffix}", code=302)
        return redirect(f"/customer/{org_slug}/{panorama_id}{suffix}", code=302)

    @app.route('/customer/<org_slug>/<int:panorama_id>')
    def customer_with_org(org_slug, panorama_id):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        panorama = get_panorama_by_id(sb, panorama_id)
        if not panorama:
            return "Panorama not found", 404
        org_name, canonical_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        qs = request.query_string.decode('utf-8') if request.query_string else ''
        suffix = ('?' + qs) if qs else ''
        if bool((panorama or {}).get('is_360')):
            return redirect(f"/customer/{canonical_slug}/3d/{panorama_id}{suffix}", code=302)
        if str(org_slug or '').lower() != str(canonical_slug).lower():
            return redirect(f"/customer/{canonical_slug}/{panorama_id}{suffix}", code=302)
        return render_customer_panorama_template(sb, panorama, org_name, canonical_slug)

    @app.route('/client/<int:panorama_id>')
    def client(panorama_id):
        sb, panorama, err = _load_panorama_for_page_mode(panorama_id, 'client')
        if err:
            return err
        org_name, org_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        return render_template('client.html', panorama=panorama, mode='client', org_name=org_name, org_slug=org_slug, **auth_ctx())

    @app.route('/admin/3d/<int:panorama_id>')
    def admin_3d(panorama_id):
        sb, panorama, err = _load_panorama_for_page_mode(panorama_id, 'admin')
        if err:
            return err
        org_name, org_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        workspace_panoramas = []
        customer_view_config = {}
        workspace_id = (panorama or {}).get('workspace_id')
        if workspace_id:
            try:
                r = sb.table('panoramas').select('id, name, filename').eq('workspace_id', workspace_id).eq('is_360', True).order('id').execute()
                rows = list(r.data or [])
                for p in rows:
                    workspace_panoramas.append({
                        'id': p.get('id'),
                        'name': (p.get('name') or '').strip() or ('Panorama #' + str(p.get('id') or '')),
                        'filename': p.get('filename') or '',
                    })
                customer_view_config = ws_get_customer_config(sb, workspace_id) or {}
            except Exception:
                pass
        return render_template('admin_3d.html', panorama=panorama, org_name=org_name, org_slug=org_slug, workspace_id=workspace_id, workspace_panoramas=workspace_panoramas, customer_view_config=customer_view_config, **auth_ctx())

    @app.route('/client/3d/<int:panorama_id>')
    def client_3d(panorama_id):
        sb, panorama, err = _load_panorama_for_page_mode(panorama_id, 'client')
        if err:
            return err
        org_name, org_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        return render_template('client_3d.html', panorama=panorama, org_name=org_name, org_slug=org_slug, **auth_ctx())

    @app.route('/customer/3d/<int:panorama_id>')
    def customer_3d(panorama_id):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        panorama = get_panorama_by_id(sb, panorama_id)
        if not panorama:
            return "Panorama not found", 404
        _org_name, org_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        qs = request.query_string.decode('utf-8') if request.query_string else ''
        suffix = ('?' + qs) if qs else ''
        is_360 = bool((panorama or {}).get('is_360'))
        if not is_360:
            return redirect(f"/customer/{org_slug}/{panorama_id}{suffix}", code=302)
        return redirect(f"/customer/{org_slug}/3d/{panorama_id}{suffix}", code=302)

    @app.route('/customer/<org_slug>/3d/<int:panorama_id>')
    def customer_3d_with_org(org_slug, panorama_id):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        panorama = get_panorama_by_id(sb, panorama_id)
        if not panorama:
            return "Panorama not found", 404
        org_name, canonical_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        qs = request.query_string.decode('utf-8') if request.query_string else ''
        suffix = ('?' + qs) if qs else ''
        if not bool((panorama or {}).get('is_360')):
            return redirect(f"/customer/{canonical_slug}/{panorama_id}{suffix}", code=302)
        if str(org_slug or '').lower() != str(canonical_slug).lower():
            return redirect(f"/customer/{canonical_slug}/3d/{panorama_id}{suffix}", code=302)
        return render_customer_panorama_template(sb, panorama, org_name, canonical_slug)

    @app.route('/customer/project/<workspace_id>')
    def customer_project_view(workspace_id):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        workspace = get_workspace_by_id(sb, workspace_id)
        if not workspace or not workspace.get('is_published'):
            return "Project not found", 404
        main_id = workspace.get('main_panorama_id')
        if not main_id:
            return "Project has no main panorama", 404
        panorama = get_panorama_by_id(sb, main_id)
        if not panorama:
            return "Panorama not found", 404
        org_name, canonical_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        # IMPORTANT: Keep this URL stable (no redirects). It should always open the
        # current workspace main panorama, even if the main panorama changes later.
        return render_customer_panorama_template(sb, panorama, org_name, canonical_slug)

    @app.route('/panoview/<endpoint>')
    def customer_project_share_view(endpoint):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        workspace_id = ws_get_workspace_id_by_share_endpoint(sb, endpoint)
        if not workspace_id:
            return "Project not found", 404
        workspace = get_workspace_by_id(sb, workspace_id)
        if not workspace or not workspace.get('is_published'):
            return "Project not found", 404
        main_id = workspace.get('main_panorama_id')
        if not main_id:
            return "Project has no main panorama", 404
        panorama = get_panorama_by_id(sb, main_id)
        if not panorama:
            return "Panorama not found", 404
        org_name, canonical_slug = get_org_name_and_slug_for_panorama(sb, panorama)
        return render_customer_panorama_template(sb, panorama, org_name, canonical_slug)

    @app.route('/uploads/<filename>')
    def uploaded_file(filename):
        size = request.args.get('size', '').strip().lower()
        use_thumb = size == 'thumb'
        use_optimized = size == 'optimized'
        opt_cache_ttl = max(int(SUPABASE_S3_SIGNED_URL_TTL), 3600)
        if use_thumb:
            thumb_url = get_panorama_thumb_s3_url(filename)
            if thumb_url:
                resp = redirect(thumb_url, code=302)
                try:
                    resp.headers['Cache-Control'] = f'private, max-age={int(SUPABASE_S3_SIGNED_URL_TTL)}'
                except Exception:
                    resp.headers['Cache-Control'] = 'private, max-age=900'
                return resp
            client = get_s3_client()
            if client:
                key = panorama_object_key(filename)
                try:
                    obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=key)
                    body = obj.get('Body')
                    data = body.read(MAX_PANORAMA_THUMB_READ_BYTES) if body else b''
                    if body:
                        try:
                            body.close()
                        except Exception:
                            pass
                    if data:
                        thumb_bytes = generate_panorama_thumb_bytes(data)
                        if thumb_bytes:
                            upload_panorama_thumb_to_s3(filename, thumb_bytes)
                            resp = Response(thumb_bytes, mimetype='image/jpeg')
                            resp.headers['Cache-Control'] = 'private, max-age=86400'
                            return resp
                except Exception:
                    pass
            local_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(local_path):
                try:
                    with open(local_path, 'rb') as f:
                        data = f.read(MAX_PANORAMA_THUMB_READ_BYTES)
                    thumb_bytes = generate_panorama_thumb_bytes(data)
                    if thumb_bytes:
                        return Response(thumb_bytes, mimetype='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})
                except Exception:
                    pass
        if use_optimized:
            opt_url = get_panorama_optimized_s3_url(filename)
            if opt_url:
                resp = redirect(opt_url, code=302)
                resp.headers['Cache-Control'] = f'private, max-age={opt_cache_ttl}'
                return resp
            client = get_s3_client()
            if client:
                key = panorama_object_key(filename)
                try:
                    obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=key)
                    body = obj.get('Body')
                    data = body.read(MAX_PANORAMA_OPTIMIZED_READ_BYTES) if body else b''
                    if body:
                        try:
                            body.close()
                        except Exception:
                            pass
                    if data:
                        opt_bytes = generate_panorama_optimized_bytes(data)
                        if opt_bytes:
                            upload_panorama_optimized_to_s3(filename, opt_bytes)
                            resp = Response(opt_bytes, mimetype='image/jpeg')
                            resp.headers['Cache-Control'] = f'private, max-age={opt_cache_ttl}'
                            return resp
                except Exception:
                    pass
        s3_url = get_panorama_s3_url(filename)
        if s3_url:
            resp = redirect(s3_url, code=302)
            try:
                resp.headers['Cache-Control'] = f'private, max-age={int(SUPABASE_S3_SIGNED_URL_TTL)}'
            except Exception:
                resp.headers['Cache-Control'] = 'private, max-age=900'
            return resp
        resp = send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)
        try:
            if use_thumb:
                resp.headers.setdefault('Cache-Control', 'public, max-age=86400')
            elif use_optimized:
                resp.headers.setdefault('Cache-Control', f'public, max-age={int(opt_cache_ttl)}')
            else:
                resp.headers.setdefault('Cache-Control', 'public, max-age=604800')
        except Exception:
            pass
        return resp

    # ----- API: public (no auth) -----
    @app.route('/api/public/panoramas/<int:panorama_id>', methods=['GET'])
    def public_get_panorama(panorama_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama = get_panorama_by_id(sb, panorama_id)
        if not panorama:
            return jsonify({'error': 'Panorama not found'}), 404
        out = dict(panorama)
        out.pop('user_id', None)
        for k in ('created_at', 'updated_at'):
            if k in out and out[k]:
                out[k] = str(out[k])
        return jsonify(out)

    @app.route('/api/public/panoramas/<int:panorama_id>/plots', methods=['GET'])
    def public_get_plots(panorama_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama = get_panorama_by_id(sb, panorama_id)
        if not panorama:
            return jsonify({'error': 'Panorama not found'}), 404
        try:
            fields_base = (
                'id, panorama_id, name, area, price, status, description, color, '
                'media_photo, media_video, points, created_at, updated_at, image_filename'
            )
            fields_with_links = (
                'id, panorama_id, name, area, price, status, description, color, '
                'media_photo, media_video, linked_panorama_id, points, created_at, updated_at, image_filename'
            )
            fields_with_label = fields_with_links + ', label_longitude, label_latitude'
            try:
                r = sb.table('plots').select(fields_with_label).eq('panorama_id', panorama_id).order('created_at').execute()
            except Exception:
                try:
                    r = sb.table('plots').select(fields_with_links).eq('panorama_id', panorama_id).order('created_at').execute()
                except Exception as e2:
                    if 'linked_panorama_id' in str(e2).lower():
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
                p['has_image'] = bool(p.get('image_filename'))
                p.pop('image_filename', None)
                if p.get('label_longitude') is not None:
                    try:
                        p['label_longitude'] = float(p['label_longitude'])
                    except (TypeError, ValueError):
                        p['label_longitude'] = None
                if p.get('label_latitude') is not None:
                    try:
                        p['label_latitude'] = float(p['label_latitude'])
                    except (TypeError, ValueError):
                        p['label_latitude'] = None
                plots.append(p)
            # Enrich with lock status
            try:
                plot_ids = [p['id'] for p in plots if p.get('id')]
                locked_ids = set()
                if plot_ids:
                    for ci in range(0, len(plot_ids), 50):
                        chunk = plot_ids[ci:ci+50]
                        lr = sb.table('plot_locks').select('plot_id').in_('plot_id', chunk).execute()
                        for lrow in (lr.data or []):
                            locked_ids.add(lrow.get('plot_id'))
                for p in plots:
                    p['is_locked'] = p.get('id') in locked_ids
            except Exception:
                for p in plots:
                    p['is_locked'] = False
            return jsonify(plots)
        except Exception as e:
            msg = str(e)
            if 'image_filename' in msg.lower() and ('does not exist' in msg.lower() or 'column' in msg.lower()):
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    @app.route('/api/public/plots/<int:plot_id>/image', methods=['GET'])
    def public_get_plot_image(plot_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            try:
                pl = sb.table('plots').select('panorama_id, image_filename, image_content_type').eq('id', plot_id).limit(1).execute()
            except Exception as e:
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
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': 'Not found'}), 404
        if panorama_id is None or not get_panorama_by_id(sb, panorama_id):
            return jsonify({'error': 'Not found'}), 404
        image_filename = row.get('image_filename') or ''
        if not image_filename:
            return jsonify({'error': 'No image'}), 404
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not configured'}), 503
        try:
            obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=plot_object_key(image_filename))
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
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama = get_panorama_by_id(sb, panorama_id)
        if not panorama:
            return jsonify({'error': 'Panorama not found'}), 404
        style_columns_supported = True
        link_columns_supported = True
        image_columns_supported = True
        base_cols = ['id', 'plot_id', 'name', 'description', 'longitude', 'latitude', 'status', 'created_at']
        style_cols = ['marker_style', 'marker_icon', 'marker_color', 'rotation_x', 'rotation_y', 'rotation_z']
        link_cols = ['linked_panorama_id']
        voiceover_columns_supported = True

        def build_columns():
            cols = list(base_cols)
            if image_columns_supported:
                cols.insert(4, 'image_filename')
            if voiceover_columns_supported:
                cols.append('voiceover_filename')
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

        def is_voiceover_column_error(err):
            msg = str(err).lower()
            return 'voiceover_filename' in msg and ('does not exist' in msg or 'column' in msg)

        last_error = None
        for _ in range(4):
            try:
                r = (
                    sb.table('plot_markers')
                    .select(build_columns())
                    .eq('plot_id', str(panorama_id))
                    .order('created_at', desc=False)
                    .execute()
                )
                markers = r.data or []
                # Enrich with lock status
                try:
                    marker_ids = [str(m.get('id', '')) for m in markers if m.get('id')]
                    locked_ids = set()
                    if marker_ids:
                        for i in range(0, len(marker_ids), 50):
                            chunk = marker_ids[i:i+50]
                            lr = sb.table('plot_locks').select('plot_id').in_('plot_id', chunk).execute()
                            for row in (lr.data or []):
                                locked_ids.add(str(row.get('plot_id', '')))
                    for m in markers:
                        m['is_locked'] = str(m.get('id', '')) in locked_ids
                except Exception:
                    # If plot_locks table doesn't exist yet, just return without lock info
                    for m in markers:
                        m['is_locked'] = False
                return jsonify(markers)
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
                if voiceover_columns_supported and is_voiceover_column_error(e):
                    voiceover_columns_supported = False
                    changed = True
                if not changed:
                    break
        return jsonify({'error': str(last_error or 'Failed to load markers')}), 500

    @app.route('/api/public/markers/<marker_id>/image', methods=['GET'])
    def public_get_marker_image(marker_id):
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
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': 'Marker not found'}), 404
        plot_id = str(row.get('plot_id') or '').strip()
        try:
            panorama_id = int(plot_id)
        except Exception:
            panorama_id = None
        if panorama_id is not None and not get_panorama_by_id(sb, panorama_id):
            return jsonify({'error': 'Not found'}), 404
        image_filename = row.get('image_filename') or ''
        if not image_filename:
            return jsonify({'error': 'No image'}), 404
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not configured'}), 503
        use_thumb = request.args.get('size') == 'thumb'
        key = marker_object_key(image_filename)
        if use_thumb:
            thumb_key = marker_thumb_object_key(image_filename)
            if thumb_key:
                try:
                    obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=thumb_key)
                    body = obj.get('Body')
                    data = body.read() if body else b''
                    try:
                        if body:
                            body.close()
                    except Exception:
                        pass
                    content_type = obj.get('ContentType') or 'image/jpeg'
                    resp = Response(data, mimetype=str(content_type or 'image/jpeg'))
                    resp.headers['Cache-Control'] = 'private, max-age=900'
                    return resp
                except Exception:
                    pass
        try:
            obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=key)
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

    @app.route('/api/public/markers/<marker_id>/voiceover', methods=['GET'])
    def public_get_marker_voiceover(marker_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            r = sb.table('plot_markers').select('id, plot_id, voiceover_filename').eq('id', marker_id).limit(1).execute()
            if not r.data:
                return jsonify({'error': 'Marker not found'}), 404
            row = r.data[0]
        except Exception as e:
            msg = str(e).lower()
            if 'voiceover_filename' in msg and ('does not exist' in msg or 'column' in msg):
                return jsonify({'error': 'voiceover_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': 'Marker not found'}), 404
        plot_id = str(row.get('plot_id') or '').strip()
        try:
            panorama_id = int(plot_id)
        except Exception:
            panorama_id = None
        if panorama_id is not None and not get_panorama_by_id(sb, panorama_id):
            return jsonify({'error': 'Not found'}), 404
        voiceover_filename = row.get('voiceover_filename') or ''
        if not voiceover_filename:
            return jsonify({'error': 'No voice-over'}), 404
        url = get_voiceover_s3_url(voiceover_filename)
        if not url:
            return jsonify({'error': 'Voice-over not found'}), 404
        return jsonify({'url': url})

    @app.route('/api/public/buy-interests', methods=['POST'])
    def public_create_buy_interest():
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
                return jsonify({'error': 'buy_interests table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
                return jsonify({'error': 'buy_interests table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    @app.route('/api/public/portal/projects', methods=['GET'])
    def public_portal_projects():
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        q = request.args.get('q', '').strip()
        city = request.args.get('city', '').strip()
        project_type = request.args.get('type', '').strip()
        sort = request.args.get('sort', 'newest').strip()
        page = max(1, int(request.args.get('page', 1)))
        per_page = min(50, max(1, int(request.args.get('per_page', 12))))
        offset_val = (page - 1) * per_page
        try:
            query = sb.table('workspaces').select('*').eq('is_published', True)
            if city:
                query = query.ilike('location_city', f'%{city}%')
            if project_type:
                query = query.eq('project_type', project_type)
            if q:
                query = query.or_(
                    f'name.ilike.%{q}%,location_address.ilike.%{q}%,location_city.ilike.%{q}%,'
                    f'builder_name.ilike.%{q}%,description.ilike.%{q}%,rera_registration.ilike.%{q}%'
                )
            if sort == 'oldest':
                query = query.order('created_at', desc=False)
            else:
                query = query.order('created_at', desc=True)
            result = query.range(offset_val, offset_val + per_page - 1).execute()
            rows = list(result.data or [])
        except Exception as e:
            msg = str(e).lower()
            if 'does not exist' in msg or 'column' in msg or '42703' in msg:
                return jsonify({'projects': [], 'total': 0, 'page': page, 'per_page': per_page, 'cities': []})
            return jsonify({'error': str(e)}), 500

        workspace_ids = [str(r.get('id')) for r in rows if r.get('id')]
        main_pano_ids = [r.get('main_panorama_id') for r in rows if r.get('main_panorama_id')]
        main_panos = {}
        if main_pano_ids:
            try:
                pano_r = sb.table('panoramas').select('id, filename, is_360').in_('id', main_pano_ids).execute()
                for p in (pano_r.data or []):
                    main_panos[int(p.get('id'))] = p
            except Exception:
                pass

        panorama_ids = []
        ws_to_panos = {}
        try:
            panos_r = sb.table('panoramas').select('id, workspace_id').in_('workspace_id', workspace_ids).execute()
            for p in (panos_r.data or []):
                ws_id = str(p.get('workspace_id') or '')
                if ws_id not in ws_to_panos:
                    ws_to_panos[ws_id] = []
                pid = p.get('id')
                ws_to_panos[ws_id].append(pid)
                panorama_ids.append(pid)
        except Exception:
            pass

        plot_stats = {}
        if panorama_ids:
            try:
                plots_result = sb.table('plots').select('panorama_id, id, price, status').in_('panorama_id', panorama_ids).execute()
                for plot in (plots_result.data or []):
                    pid = plot.get('panorama_id')
                    if pid not in plot_stats:
                        plot_stats[pid] = {'count': 0, 'prices': [], 'available': 0, 'sold': 0, 'hold': 0}
                    stats = plot_stats[pid]
                    stats['count'] += 1
                    status = (plot.get('status') or '').lower()
                    if status == 'available':
                        stats['available'] += 1
                    elif status == 'sold':
                        stats['sold'] += 1
                    elif status in ('on_hold', 'reserved'):
                        stats['hold'] += 1
                    price_str = str(plot.get('price') or '').strip()
                    if price_str:
                        try:
                            numeric = float(''.join(c for c in price_str if c.isdigit() or c == '.'))
                            if numeric > 0:
                                stats['prices'].append(numeric)
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass

        ws_plot_stats = {}
        for ws_id, pano_ids in ws_to_panos.items():
            agg = {'count': 0, 'prices': [], 'available': 0, 'sold': 0, 'hold': 0}
            for pid in pano_ids:
                s = plot_stats.get(pid, {})
                agg['count'] += s.get('count', 0)
                agg['available'] += s.get('available', 0)
                agg['sold'] += s.get('sold', 0)
                agg['hold'] += s.get('hold', 0)
                agg['prices'].extend(s.get('prices', []))
            ws_plot_stats[ws_id] = agg

        org_ids = list(set(r.get('org_id') for r in rows if r.get('org_id')))
        org_map = {}
        if org_ids:
            try:
                org_result = sb.table('organizations').select('id, name, accent_color').in_('id', org_ids).execute()
                for org in (org_result.data or []):
                    org_map[org['id']] = org
            except Exception:
                pass

        projects = []
        for row in rows:
            ws_id = str(row.get('id'))
            main_id = row.get('main_panorama_id')
            main_pano = main_panos.get(int(main_id), {}) if main_id else {}
            stats = ws_plot_stats.get(ws_id, {'count': 0, 'prices': [], 'available': 0, 'sold': 0, 'hold': 0})
            prices = stats['prices']
            avg_price = sum(prices) / len(prices) if prices else 0
            min_price = min(prices) if prices else 0
            max_price = max(prices) if prices else 0
            org = org_map.get(row.get('org_id'), {})
            amenities = row.get('amenities') or []
            if isinstance(amenities, str):
                try:
                    amenities = json.loads(amenities)
                except Exception:
                    amenities = []

            projects.append({
                'id': ws_id,
                'main_panorama_id': int(main_id) if main_id else None,
                'name': row.get('name', ''),
                'filename': main_pano.get('filename', ''),
                'is_360': bool(main_pano.get('is_360')),
                'location_address': row.get('location_address', ''),
                'location_city': row.get('location_city', ''),
                'location_state': row.get('location_state', ''),
                'location_lat': row.get('location_lat'),
                'location_lng': row.get('location_lng'),
                'rera_registration': row.get('rera_registration', ''),
                'launch_date': str(row.get('launch_date') or ''),
                'possession_date': row.get('possession_date', ''),
                'builder_name': row.get('builder_name', ''),
                'project_type': row.get('project_type', 'residential'),
                'total_area': row.get('total_area', ''),
                'description': row.get('description', ''),
                'amenities': amenities,
                'contact_phone': row.get('contact_phone', ''),
                'contact_email': row.get('contact_email', ''),
                'google_maps_link': row.get('google_maps_link', ''),
                'org_name': org.get('name', ''),
                'org_accent': org.get('accent_color', '#c9a962'),
                'plot_count': stats['count'],
                'available_plots': stats['available'],
                'sold_plots': stats['sold'],
                'hold_plots': stats['hold'],
                'avg_price': round(avg_price, 2),
                'min_price': round(min_price, 2),
                'max_price': round(max_price, 2),
                'created_at': str(row.get('created_at') or ''),
                'updated_at': str(row.get('updated_at') or ''),
            })

        total = len(projects)
        if len(rows) == per_page:
            try:
                count_q = sb.table('workspaces').select('id', count='exact').eq('is_published', True)
                if city:
                    count_q = count_q.ilike('location_city', f'%{city}%')
                if project_type:
                    count_q = count_q.eq('project_type', project_type)
                if q:
                    count_q = count_q.or_(
                        f'name.ilike.%{q}%,location_address.ilike.%{q}%,location_city.ilike.%{q}%,'
                        f'builder_name.ilike.%{q}%,description.ilike.%{q}%'
                    )
                count_result = count_q.execute()
                total = getattr(count_result, 'count', None) or len(projects)
            except Exception:
                total = offset_val + len(projects) + 1

        cities = []
        try:
            city_q = sb.table('workspaces').select('location_city').eq('is_published', True)
            city_result = city_q.execute()
            city_set = set()
            for r in (city_result.data or []):
                c = (r.get('location_city') or '').strip()
                if c and c not in city_set:
                    city_set.add(c)
                    cities.append(c)
            cities.sort()
        except Exception:
            pass

        return jsonify({
            'projects': projects,
            'total': total,
            'page': page,
            'per_page': per_page,
            'cities': cities,
        })

    # ----- API: require Authorization Bearer token -----
    @app.route('/api/panoramas', methods=['GET'])
    @require_auth
    def get_panoramas(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        items = list_panoramas_for_user(sb, user_id)
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
            out = ws_list_workspaces(sb, user_id)
            return jsonify(out)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workspaces', methods=['POST'])
    @require_admin
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
        project_fields = project_fields_from_payload(payload)
        try:
            ws = ws_create_workspace(sb, user_id, name, **project_fields)
            return jsonify({'success': True, 'workspace': ws}), 201
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            msg = str(e).lower()
            if 'duplicate' in msg or 'unique' in msg or 'already exists' in msg:
                return jsonify({'error': 'Workspace name already exists'}), 409
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workspaces/<workspace_id>', methods=['GET'])
    @require_auth
    def get_workspace(user_id, role, workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            workspace = get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        access = 'owner' if str(workspace.get('user_id') or '') == str(user_id) else None
        if access is None:
            shared = sb.table('workspace_access').select('access_type').eq('workspace_id', workspace_id).eq('user_id', user_id).limit(1).execute()
            if not (shared.data and len(shared.data) > 0):
                return jsonify({'error': 'Workspace not found'}), 404
            access = (shared.data[0].get('access_type') or 'viewer')
        ws = serialize_workspace_row(workspace, access)
        return jsonify(ws)

    @app.route('/api/workspaces/<workspace_id>', methods=['PATCH', 'PUT'])
    @require_admin
    def rename_workspace(user_id, role, workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        payload = request.get_json(silent=True) or request.form or {}
        name = str(payload.get('name') or '').strip() if payload.get('name') is not None else None
        main_panorama_id = payload.get('main_panorama_id')
        if main_panorama_id is not None:
            try:
                main_panorama_id = int(main_panorama_id)
            except (TypeError, ValueError):
                main_panorama_id = None
        project_fields = project_fields_from_payload(payload)
        if not name and main_panorama_id is None and not project_fields:
            return jsonify({'error': 'Provide at least one field to update'}), 400
        if name is not None and len(name) > 120:
            return jsonify({'error': 'name must be 120 characters or fewer'}), 400
        try:
            workspace = get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        if str(workspace.get('user_id') or '') != str(user_id):
            return jsonify({'error': 'Only workspace owner can update'}), 403
        try:
            ws = ws_update_workspace(sb, workspace_id, user_id, name=name, main_panorama_id=main_panorama_id, **project_fields)
            return jsonify({'success': True, 'workspace': ws})
        except ValueError as ve:
            err = str(ve)
            if 'Panorama' in err or 'workspace' in err.lower():
                return jsonify({'error': err}), 400
            if 'name' in err.lower():
                return jsonify({'error': err}), 400
            return jsonify({'error': err}), 400
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            msg = str(e).lower()
            if 'duplicate' in msg or 'unique' in msg or 'already exists' in msg:
                return jsonify({'error': 'Workspace name already exists'}), 409
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workspaces/<workspace_id>', methods=['DELETE'])
    @require_admin
    def delete_workspace(user_id, role, workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            workspace = get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        if str(workspace.get('user_id') or '') != str(user_id):
            return jsonify({'error': 'Only workspace owner can delete'}), 403
        try:
            ok = ws_delete_workspace(sb, workspace_id, user_id)
            if ok:
                return jsonify({'success': True})
            return jsonify({'error': 'Delete failed'}), 500
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workspaces/<workspace_id>/customer-view-config', methods=['GET'])
    def get_workspace_customer_view_config_public(workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            workspace = get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        config = ws_get_customer_config(sb, workspace_id)
        return jsonify({'success': True, 'config': config or {}})

    @app.route('/api/workspaces/<workspace_id>/customer-config', methods=['GET'])
    @require_admin
    def get_workspace_customer_config(user_id, role, workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            workspace = get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        if not can_manage_workspace(sb, workspace, user_id, role):
            return jsonify({'error': 'Forbidden'}), 403
        config = ws_get_customer_config(sb, workspace_id)
        return jsonify({'success': True, 'config': config or {}})

    @app.route('/api/workspaces/<workspace_id>/customer-config', methods=['PATCH', 'PUT'])
    @require_admin
    def update_workspace_customer_config(user_id, role, workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        payload = request.get_json(silent=True) or {}
        config = payload.get('config')
        if not isinstance(config, dict):
            return jsonify({'error': 'config must be a JSON object'}), 400
        try:
            result = ws_update_customer_config(sb, workspace_id, user_id, role, config)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not result:
            return jsonify({'error': 'Workspace not found or forbidden'}), 404
        return jsonify({'success': True, 'config': config})

    @app.route('/api/workspaces/share-endpoint/check', methods=['POST'])
    @require_admin
    def check_workspace_share_endpoint(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        payload = request.get_json(silent=True) or {}
        endpoint = str(payload.get('endpoint') or '')
        workspace_id = str(payload.get('workspace_id') or '').strip() or None
        normalized = ws_normalize_workspace_share_endpoint(endpoint)
        if normalized is None:
            return jsonify({'success': True, 'available': False, 'normalized_endpoint': None, 'error': 'Endpoint must use only lowercase letters, numbers, hyphens, and be 3-63 chars'})
        if not normalized:
            return jsonify({'success': True, 'available': False, 'normalized_endpoint': '', 'error': 'Endpoint is required'})
        try:
            if workspace_id:
                workspace = get_workspace_by_id(sb, workspace_id)
                if not workspace:
                    return jsonify({'error': 'Workspace not found'}), 404
                if not can_manage_workspace(sb, workspace, user_id, role):
                    return jsonify({'error': 'Forbidden'}), 403
            available = ws_is_workspace_share_endpoint_available(sb, normalized, workspace_id)
            return jsonify({'success': True, 'available': bool(available), 'normalized_endpoint': normalized})
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workspaces/<workspace_id>/share-endpoint', methods=['GET'])
    @require_auth
    def get_workspace_share_endpoint(user_id, role, workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            workspace = get_workspace_by_id(sb, workspace_id)
            if not workspace:
                return jsonify({'error': 'Workspace not found'}), 404
            # Allow owner/admin OR users with workspace access (client/viewer)
            has_access = can_manage_workspace(sb, workspace, user_id, role)
            if not has_access:
                try:
                    acc = sb.table('workspace_access').select('access_type').eq('workspace_id', workspace_id).eq('user_id', user_id).limit(1).execute()
                    has_access = bool(acc.data and len(acc.data) > 0)
                except Exception:
                    pass
            if not has_access:
                return jsonify({'error': 'Forbidden'}), 403
            row = ws_get_workspace_share_endpoint(sb, workspace_id) or {}
            payload = _workspace_share_payload(workspace_id, row.get('endpoint'))
            return jsonify({'success': True, 'share': payload})
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workspaces/<workspace_id>/share-endpoint', methods=['PATCH', 'PUT'])
    @require_admin
    def update_workspace_share_endpoint(user_id, role, workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        payload = request.get_json(silent=True) or {}
        endpoint = payload.get('endpoint')
        try:
            result = ws_update_workspace_share_endpoint(sb, workspace_id, user_id, role, endpoint)
            if not result:
                return jsonify({'error': 'Workspace not found or forbidden'}), 404
            share = _workspace_share_payload(workspace_id, result.get('endpoint'))
            return jsonify({'success': True, 'share': share})
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workspaces/<workspace_id>/access', methods=['GET'])
    @require_auth
    def get_workspace_access(user_id, role, workspace_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            workspace = get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        if not can_manage_workspace(sb, workspace, user_id, role):
            return jsonify({'error': 'Only owner or admin can list access'}), 403
        try:
            r = sb.table('workspace_access').select('*').eq('workspace_id', workspace_id).execute()
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
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
            workspace = get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        if not can_manage_workspace(sb, workspace, user_id, role):
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
            return jsonify({'success': True}), 201
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workspaces/<workspace_id>/access/<target_user_id>', methods=['DELETE'])
    @require_auth
    def revoke_workspace_access(user_id, role, workspace_id, target_user_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            workspace = get_workspace_by_id(sb, workspace_id)
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        if not workspace:
            return jsonify({'error': 'Workspace not found'}), 404
        if not can_manage_workspace(sb, workspace, user_id, role):
            return jsonify({'error': 'Only owner or admin can revoke access'}), 403
        try:
            sb.table('workspace_access').delete().eq('workspace_id', workspace_id).eq('user_id', target_user_id).execute()
            return jsonify({'success': True})
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/panoramas/upload-url', methods=['POST'])
    @require_admin
    def create_panorama_upload_url(user_id, role):
        if not use_s3():
            return jsonify({'error': 'Supabase S3 is not configured'}), 503
        payload = request.get_json(silent=True) or request.form or {}
        original_filename = str(payload.get('original_filename') or payload.get('filename') or '').strip()
        if not original_filename:
            return jsonify({'error': 'original_filename is required'}), 400
        max_bytes = current_app.config.get('MAX_CONTENT_LENGTH') or 0
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
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not fully configured'}), 503
        key = panorama_object_key(filename)
        try:
            upload_url = client.generate_presigned_url(
                ClientMethod='put_object',
                Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': key, 'ContentType': image_content_type},
                ExpiresIn=int(SUPABASE_S3_UPLOAD_URL_TTL),
            )
        except Exception as e:
            current_app.logger.exception('Failed to generate panorama upload URL')
            return jsonify({'error': str(e)}), 500
        token = panorama_upload_serializer(secret_key).dumps({
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
    @require_admin
    def create_panorama(user_id, role):
        def _coerce_dim(value):
            try:
                n = int(str(value).strip())
                if n < 0:
                    return 0
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
        if file:
            if use_s3():
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
                if use_s3():
                    upload_panorama_to_s3(filename, file.stream, image_content_type)
                    stored_ok = True
                else:
                    local_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                    file.save(local_filepath)
                    stored_ok = True
            except Exception as e:
                current_app.logger.exception('Panorama file storage failed')
                return jsonify({'error': str(e)}), 500
        else:
            filename = os.path.basename(str(payload.get('filename') or '')).strip()
            upload_token = str(payload.get('upload_token') or '').strip()
            if not filename:
                return jsonify({'error': 'filename is required'}), 400
            if not upload_token:
                return jsonify({'error': 'upload_token is required'}), 400
            if not allowed_file(filename):
                return jsonify({'error': 'File type not allowed'}), 400
            if not use_s3():
                return jsonify({'error': 'Supabase S3 is not configured'}), 503
            try:
                signed = panorama_upload_serializer(secret_key).loads(
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
            client = get_s3_client()
            if not client:
                return jsonify({'error': 'Supabase S3 is not fully configured'}), 503
            try:
                head = client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=panorama_object_key(filename))
                stored_ok = True
            except Exception:
                return jsonify({'error': 'Upload not found. Re-upload and try again.'}), 400
            try:
                size_bytes = int(head.get('ContentLength') or 0)
            except Exception:
                size_bytes = 0
            max_bytes = current_app.config.get('MAX_CONTENT_LENGTH') or 0
            if max_bytes and size_bytes and size_bytes > max_bytes:
                delete_panorama_from_s3(filename)
                max_mb = max(1, int(max_bytes / (1024 * 1024)))
                return jsonify({'error': f'Upload too large (max {max_mb}MB)'}), 413
            try:
                obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=panorama_object_key(filename))
                body = obj.get('Body')
                data = body.read(MAX_PANORAMA_THUMB_READ_BYTES) if body else b''
                if body:
                    try:
                        body.close()
                    except Exception:
                        pass
                if data:
                    thumb_bytes = generate_panorama_thumb_bytes(data)
                    if thumb_bytes:
                        upload_panorama_thumb_to_s3(filename, thumb_bytes)
                    opt_bytes = generate_panorama_optimized_bytes(data)
                    if opt_bytes:
                        upload_panorama_optimized_to_s3(filename, opt_bytes)
            except Exception:
                pass
            original_filename = secure_filename(str(payload.get('original_filename') or filename)) or 'upload'
        try:
            org_id = None
            try:
                profile = get_profile(sb, user_id) or {}
                org_id = profile.get('org_id')
            except Exception:
                org_id = None
            if workspace_id:
                try:
                    workspace = get_workspace_by_id(sb, workspace_id)
                except Exception as e:
                    if is_workspace_schema_missing(e):
                        return _ws_error_response()
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

            # Auto-set as main if this is a 360 panorama in a folder with no main
            if workspace_id and is_360:
                try:
                    ws_row = get_workspace_by_id(sb, workspace_id)
                    if ws_row and not ws_row.get('main_panorama_id'):
                        sb.table('workspaces').update({
                            'main_panorama_id': panorama_id,
                            'updated_at': datetime.utcnow().isoformat(),
                        }).eq('id', workspace_id).execute()
                except Exception:
                    pass
                try:
                    ws_ensure_panorama_added_to_workspace_config(sb, workspace_id, panorama_id, user_id, role)
                except Exception:
                    pass

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
            current_app.logger.exception('Panorama upload failed')
            if stored_ok:
                if use_s3():
                    delete_panorama_from_s3(filename)
                elif local_filepath and os.path.exists(local_filepath):
                    try:
                        os.remove(local_filepath)
                    except Exception:
                        pass
            return jsonify({'error': str(e)}), 500

    @app.route('/api/panoramas/<int:panorama_id>', methods=['DELETE'])
    @require_admin
    def delete_panorama(user_id, role, panorama_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
        if not panorama:
            return jsonify({'error': 'Panorama not found'}), 404
        if not can_delete_panorama(access_type):
            return jsonify({'error': 'Only the owner can delete this panorama'}), 403

        # If deleting the main panorama, set a new main first so customer links keep working
        payload = request.get_json(silent=True) or {}
        new_main_id = payload.get('new_main_panorama_id')
        workspace_id = panorama.get('workspace_id')
        if new_main_id and workspace_id:
            try:
                new_main_id = int(new_main_id)
                # Verify the new main panorama exists and belongs to the same workspace
                new_pano = get_panorama_by_id(sb, new_main_id)
                if not new_pano:
                    return jsonify({'error': 'New main panorama not found'}), 400
                if str(new_pano.get('workspace_id') or '') != str(workspace_id):
                    return jsonify({'error': 'New main panorama must be in the same project'}), 400
                # Update workspace main_panorama_id to the new panorama
                sb.table('workspaces').update({
                    'main_panorama_id': new_main_id,
                    'updated_at': datetime.utcnow().isoformat()
                }).eq('id', workspace_id).execute()
            except (TypeError, ValueError):
                return jsonify({'error': 'Invalid new_main_panorama_id'}), 400
        else:
            # No replacement specified — clear the main reference
            clear_workspace_main_for_panorama(sb, panorama_id)

        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], panorama['filename'])
        delete_panorama_from_s3(panorama['filename'])
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

    @app.route('/api/panoramas/<int:panorama_id>/generate-optimized', methods=['POST'])
    @require_admin
    def generate_panorama_optimized(user_id, role, panorama_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama = get_panorama_by_id(sb, panorama_id)
        if not panorama:
            return jsonify({'error': 'Panorama not found'}), 404
        filename = panorama.get('filename')
        if not filename:
            return jsonify({'error': 'No file associated'}), 400
        opt_url = get_panorama_optimized_s3_url(filename)
        if opt_url:
            return jsonify({'success': True, 'exists': True})
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'S3 not configured'}), 503
        try:
            obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=panorama_object_key(filename))
            body = obj.get('Body')
            data = body.read(MAX_PANORAMA_OPTIMIZED_READ_BYTES) if body else b''
            if body:
                try:
                    body.close()
                except Exception:
                    pass
            if not data:
                return jsonify({'error': 'Could not read source image'}), 500
            opt_bytes = generate_panorama_optimized_bytes(data)
            if not opt_bytes:
                return jsonify({'error': 'Optimized version not smaller than original; original is already efficient'}), 200
            upload_panorama_optimized_to_s3(filename, opt_bytes)
            return jsonify({'success': True, 'size': len(opt_bytes), 'original_size': len(data)})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/panoramas/<int:panorama_id>/mobile', methods=['GET'])
    @require_auth
    def get_mobile_panorama(user_id, role, panorama_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
        if not panorama or str((panorama or {}).get('source_table') or 'panoramas') != 'panoramas':
            return jsonify({'error': 'Panorama not found'}), 404
        if not can_edit_plots(access_type):
            return jsonify({'error': 'Forbidden'}), 403
        mobile = get_mobile_panorama_by_parent_id(sb, panorama_id)
        return jsonify({'mobile_panorama': mobile})

    @app.route('/api/panoramas/<int:panorama_id>/mobile', methods=['POST', 'PUT'])
    @require_admin
    def create_or_update_mobile_panorama(user_id, role, panorama_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        parent, access_type = get_panorama_with_access(sb, panorama_id, user_id)
        if not parent or str((parent or {}).get('source_table') or 'panoramas') != 'panoramas':
            return jsonify({'error': 'Panorama not found'}), 404
        if str(parent.get('user_id') or '') != str(user_id) and access_type != 'owner':
            return jsonify({'error': 'Only owner can manage mobile image'}), 403
        file = request.files.get('file')
        if not file or not getattr(file, 'filename', ''):
            return jsonify({'error': 'file is required'}), 400
        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed'}), 400
        name = str((request.form or {}).get('name') or (parent.get('name') or 'Mobile Panorama')).strip()
        if len(name) > 120:
            name = name[:120]
        original_filename = secure_filename(file.filename) or 'upload'
        ext = original_filename.rsplit('.', 1)[1].lower()
        safe_project = secure_filename(name) or 'mobile_panorama'
        unique = uuid.uuid4().hex[:10]
        filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{safe_project}_{unique}.{ext}"
        image_content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
        stored_ok = False
        local_filepath = None
        try:
            try:
                w, h = probe_image_dimensions(file.stream)
            except Exception:
                w, h = 0, 0
            if use_s3():
                upload_panorama_to_s3(filename, file.stream, image_content_type)
            else:
                local_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                file.save(local_filepath)
            stored_ok = True
            existing = get_mobile_panorama_by_parent_id(sb, panorama_id)
            row = {
                'user_id': parent.get('user_id'),
                'org_id': parent.get('org_id'),
                'workspace_id': parent.get('workspace_id'),
                'panorama_parent_id': panorama_id,
                'name': name,
                'filename': filename,
                'original_filename': original_filename,
                'width': int(w or 0),
                'height': int(h or 0),
                'is_360': False,
                'use_animated_icons': False,
                'start_view': None,
                'image_content_type': image_content_type,
                'updated_at': datetime.utcnow().isoformat(),
            }
            if existing and existing.get('id'):
                sb.table('mobile_panoramas').update(row).eq('id', existing.get('id')).execute()
                mobile_id = existing.get('id')
                old_filename = str(existing.get('filename') or '').strip()
                if old_filename and old_filename != filename:
                    delete_panorama_from_s3(old_filename)
                    old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], old_filename)
                    if os.path.isfile(old_path):
                        try:
                            os.remove(old_path)
                        except Exception:
                            pass
            else:
                row['created_at'] = datetime.utcnow().isoformat()
                r = sb.table('mobile_panoramas').insert(row).execute()
                if not r.data:
                    raise RuntimeError('Insert failed')
                mobile_id = r.data[0].get('id')
            mobile = get_mobile_panorama_by_parent_id(sb, panorama_id)
            return jsonify({'success': True, 'id': mobile_id, 'mobile_panorama': mobile})
        except Exception as e:
            if stored_ok:
                delete_panorama_from_s3(filename)
                if local_filepath and os.path.exists(local_filepath):
                    try:
                        os.remove(local_filepath)
                    except Exception:
                        pass
            return jsonify({'error': str(e)}), 500

    @app.route('/api/panoramas/<int:panorama_id>', methods=['PUT', 'PATCH'])
    @require_auth
    def rename_panorama(user_id, role, panorama_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
        # Fallback: allow admins / superadmins who have CRM access
        if not panorama:
            normalized_role = str(role or 'user').strip().lower()
            if normalized_role in ('admin', 'superadmin'):
                crm_ids = _crm_panorama_ids(sb, user_id, role)
                if panorama_id in crm_ids:
                    try:
                        r = sb.table('panoramas').select('*').eq('id', panorama_id).limit(1).execute()
                        if r.data and len(r.data) > 0:
                            panorama = r.data[0]
                            access_type = 'owner'
                    except Exception:
                        pass
        if not panorama:
            return jsonify({'error': 'Panorama not found'}), 404
        if not can_edit_plots(access_type):
            return jsonify({'error': 'You do not have permission to edit this panorama'}), 403
        payload = request.get_json(silent=True) or request.form or {}
        name = str(payload.get('name', '')).strip() if payload.get('name') is not None else None
        use_animated_icons = payload.get('use_animated_icons')
        start_view = payload.get('start_view') if 'start_view' in payload else None
        if use_animated_icons is not None:
            use_animated_icons = bool(use_animated_icons) if use_animated_icons not in (True, False) else use_animated_icons
        update_fields = {'updated_at': datetime.utcnow().isoformat()}
        if name is not None:
            if not name:
                return jsonify({'error': 'Name is required'}), 400
            if len(name) > 120:
                return jsonify({'error': 'Name must be 120 characters or fewer'}), 400
            update_fields['name'] = name
        if use_animated_icons is not None:
            update_fields['use_animated_icons'] = use_animated_icons
        if start_view is not None:
            if start_view is False:
                update_fields['start_view'] = None
            elif start_view is None:
                update_fields['start_view'] = None
            elif isinstance(start_view, dict):
                lon = start_view.get('longitude')
                lat = start_view.get('latitude')
                zoom = start_view.get('zoom')
                try:
                    lon = float(lon) if lon is not None else None
                    lat = float(lat) if lat is not None else None
                    zoom = float(zoom) if zoom is not None else None
                except Exception:
                    return jsonify({'error': 'Invalid start_view'}), 400
                if lon is None or lat is None:
                    return jsonify({'error': 'start_view must include longitude and latitude'}), 400
                next_view = {'longitude': lon, 'latitude': lat}
                if zoom is not None:
                    next_view['zoom'] = zoom
                update_fields['start_view'] = next_view
            else:
                return jsonify({'error': 'Invalid start_view'}), 400
        if len(update_fields) <= 1:
            return jsonify({'error': 'Provide at least one field to update'}), 400
        try:
            response = sb.table('panoramas').update(update_fields).eq('id', panorama_id).execute()
            err = getattr(response, 'error', None)
            if err:
                message = getattr(err, 'message', None) or str(err)
                return jsonify({'error': message}), 500
            row = None
            data = getattr(response, 'data', None)
            if isinstance(data, list) and data:
                row = data[0]
            if not row:
                try:
                    fetch = sb.table('panoramas').select('id, name, updated_at, use_animated_icons').eq('id', panorama_id).limit(1).execute()
                except Exception:
                    fetch = sb.table('panoramas').select('id, name, updated_at').eq('id', panorama_id).limit(1).execute()
                ferr = getattr(fetch, 'error', None)
                if ferr:
                    message = getattr(ferr, 'message', None) or str(ferr)
                    return jsonify({'error': message}), 500
                fdata = getattr(fetch, 'data', None)
                if isinstance(fdata, list) and fdata:
                    row = fdata[0]
            if row:
                out = {
                    'id': row.get('id', panorama_id),
                    'name': row.get('name', name) if name is not None else row.get('name'),
                    'updated_at': str(row.get('updated_at') or datetime.utcnow().isoformat()),
                }
                if 'use_animated_icons' in row:
                    out['use_animated_icons'] = bool(row.get('use_animated_icons'))
                if 'start_view' in row:
                    out['start_view'] = row.get('start_view')
                return jsonify(out)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        out = {'id': panorama_id, 'updated_at': datetime.utcnow().isoformat()}
        if name is not None:
            out['name'] = name
        if use_animated_icons is not None:
            out['use_animated_icons'] = use_animated_icons
        if start_view is not None:
            out['start_view'] = update_fields.get('start_view')
        return jsonify(out)

    # ── Resolve Google Maps short links & reverse-geocode ──
    @app.route('/api/resolve-maps-link', methods=['POST'])
    @require_auth
    def resolve_maps_link(user_id, role):
        payload = request.get_json(silent=True) or {}
        link = str(payload.get('link') or '').strip()
        if not link:
            return jsonify({'error': 'No link provided'}), 400

        # Follow redirects for short URLs (maps.app.goo.gl, goo.gl, etc.)
        resolved_url = link
        if 'goo.gl/' in link or 'maps.app.goo.gl/' in link:
            try:
                resp = _requests.head(link, allow_redirects=True, timeout=10,
                                      headers={'User-Agent': 'Mozilla/5.0'})
                resolved_url = resp.url
            except Exception:
                try:
                    resp = _requests.get(link, allow_redirects=True, timeout=10,
                                         stream=True,
                                         headers={'User-Agent': 'Mozilla/5.0'})
                    resolved_url = resp.url
                    resp.close()
                except Exception:
                    return jsonify({'error': 'Could not resolve short URL'}), 400

        # Extract coordinates from the resolved URL
        coords = None
        patterns = [
            r'@(-?\d+\.\d+),(-?\d+\.\d+)',
            r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)',
            r'q=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'place/(-?\d+\.\d+),(-?\d+\.\d+)',
            r'll=(-?\d+\.\d+),(-?\d+\.\d+)',
            r'center=(-?\d+\.\d+),(-?\d+\.\d+)',
        ]
        for pat in patterns:
            m = re.search(pat, resolved_url)
            if m:
                coords = {'lat': float(m.group(1)), 'lng': float(m.group(2))}
                break

        if not coords:
            return jsonify({'error': 'Could not extract coordinates from link', 'resolved_url': resolved_url}), 400

        result = {'lat': coords['lat'], 'lng': coords['lng'], 'resolved_url': resolved_url}

        # Reverse geocode using Nominatim (free, no API key needed)
        try:
            geo_resp = _requests.get(
                'https://nominatim.openstreetmap.org/reverse',
                params={'lat': coords['lat'], 'lon': coords['lng'], 'format': 'json', 'addressdetails': '1'},
                headers={'User-Agent': 'RealEstatePanorama/1.0'},
                timeout=8,
            )
            if geo_resp.status_code == 200:
                geo = geo_resp.json()
                addr = geo.get('address', {})
                # Build readable address
                address_parts = []
                for key in ('road', 'neighbourhood', 'suburb', 'hamlet', 'village'):
                    if addr.get(key):
                        address_parts.append(addr[key])
                result['address'] = ', '.join(address_parts) if address_parts else geo.get('display_name', '')
                result['city'] = addr.get('city') or addr.get('town') or addr.get('village') or addr.get('county') or ''
                result['state'] = addr.get('state') or ''
        except Exception:
            pass

        return jsonify(result)

    @app.route('/api/panoramas/<int:panorama_id>/workspace', methods=['PATCH', 'PUT'])
    @require_admin
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
                workspace = get_workspace_by_id(sb, workspace_id)
            except Exception as e:
                if is_workspace_schema_missing(e):
                    return _ws_error_response()
                return jsonify({'error': str(e)}), 500
            if not workspace:
                return jsonify({'error': 'Workspace not found'}), 404
            if str(workspace.get('user_id') or '') != str(user_id):
                return jsonify({'error': 'You can only move to your own workspace'}), 403
            panorama_org = panorama.get('org_id')
            workspace_org = workspace.get('org_id')
            if panorama_org and workspace_org and str(panorama_org) != str(workspace_org):
                return jsonify({'error': 'Workspace organization mismatch'}), 403
        clear_workspace_main_for_panorama(sb, panorama_id)
        try:
            r = (
                sb.table('panoramas')
                .update({'workspace_id': workspace_id, 'updated_at': datetime.utcnow().isoformat()})
                .eq('id', panorama_id)
                .execute()
            )
        except Exception as e:
            if is_workspace_schema_missing(e):
                return _ws_error_response()
            return jsonify({'error': str(e)}), 500
        row = (r.data or [None])[0] if hasattr(r, 'data') else None
        current_workspace_id = (row or {}).get('workspace_id')

        # Auto-set as main if this is a 360 panorama dropped into a folder with no main
        if workspace_id and panorama.get('is_360'):
            try:
                ws_row = get_workspace_by_id(sb, workspace_id)
                if ws_row and not ws_row.get('main_panorama_id'):
                    sb.table('workspaces').update({
                        'main_panorama_id': panorama_id,
                        'updated_at': datetime.utcnow().isoformat(),
                    }).eq('id', workspace_id).execute()
            except Exception:
                pass
            try:
                ws_ensure_panorama_added_to_workspace_config(sb, workspace_id, panorama_id, user_id, role)
            except Exception:
                pass

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
            plots = fetch_plots(sb, panorama_id)
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
            plot_id = plot_create(sb, panorama_id, data)
            return jsonify({'id': plot_id, **data}), 201
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/plots/<int:plot_id>', methods=['DELETE'])
    @require_auth
    def delete_plot(user_id, role, plot_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            pl = sb.table('plots').select('panorama_id, image_filename').eq('id', plot_id).limit(1).execute()
            if not pl.data or len(pl.data) == 0:
                return jsonify({'error': 'Plot not found'}), 404
            panorama_id = pl.data[0]['panorama_id']
        except Exception:
            return jsonify({'error': 'Not found'}), 404
        panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
        if not panorama or not can_edit_plots(access_type):
            return jsonify({'error': 'Forbidden'}), 403
        try:
            plot_delete(sb, plot_id)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/plots/<int:plot_id>', methods=['PUT'])
    @require_auth
    def update_plot(user_id, role, plot_id):
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Body required'}), 400
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama_id = get_plot_panorama_id(sb, plot_id)
        if not panorama_id:
            return jsonify({'error': 'Plot not found'}), 404
        panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
        if not panorama or not can_edit_plots(access_type):
            return jsonify({'error': 'Forbidden'}), 403
        try:
            plot_update(sb, plot_id, data)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/plots/<int:plot_id>/label-position', methods=['PATCH', 'PUT'])
    @require_auth
    def update_plot_label_position(user_id, role, plot_id):
        data = request.get_json(silent=True) or {}
        try:
            lon = data.get('label_longitude') if 'label_longitude' in data else data.get('longitude')
            lat = data.get('label_latitude') if 'label_latitude' in data else data.get('latitude')
        except Exception:
            lon, lat = None, None
        if lon is None and lat is None:
            return jsonify({'error': 'label_longitude and label_latitude (or longitude and latitude) required'}), 400
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama_id = get_plot_panorama_id(sb, plot_id)
        if not panorama_id:
            return jsonify({'error': 'Plot not found'}), 404
        panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
        if not panorama or not can_edit_plots(access_type):
            return jsonify({'error': 'Forbidden'}), 403
        try:
            lon_f = float(lon) if lon is not None else None
            lat_f = float(lat) if lat is not None else None
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid longitude or latitude'}), 400
        try:
            plot_update_label_position(sb, plot_id, lon_f, lat_f)
            return jsonify({'success': True, 'label_longitude': lon_f, 'label_latitude': lat_f})
        except Exception as e:
            if 'label_longitude' in str(e).lower() or 'label_latitude' in str(e).lower():
                return jsonify({'error': 'Plot label position not supported. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': str(e)}), 500

    @app.route('/api/plots/<int:plot_id>/image', methods=['GET'])
    @require_auth
    def get_plot_image(user_id, role, plot_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            try:
                pl = sb.table('plots').select('panorama_id, image_filename, image_content_type').eq('id', plot_id).limit(1).execute()
            except Exception as e:
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
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': 'Not found'}), 404
        panorama, _ = get_panorama_with_access(sb, panorama_id, user_id)
        if not panorama:
            return jsonify({'error': 'Forbidden'}), 403
        image_filename = row.get('image_filename') or ''
        if not image_filename:
            return jsonify({'error': 'No image'}), 404
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not configured'}), 503
        try:
            obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=plot_object_key(image_filename))
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
        if not use_s3():
            return jsonify({'error': 'Supabase S3 is not configured'}), 503
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama_id = get_plot_panorama_id(sb, plot_id)
        if not panorama_id:
            return jsonify({'error': 'Plot not found'}), 404
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
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not fully configured'}), 503
        try:
            upload_url = client.generate_presigned_url(
                ClientMethod='put_object',
                Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': plot_object_key(filename), 'ContentType': content_type},
                ExpiresIn=int(SUPABASE_S3_UPLOAD_URL_TTL),
            )
        except Exception as e:
            current_app.logger.exception('Failed to generate plot image upload URL')
            return jsonify({'error': str(e)}), 500
        token = plot_upload_serializer(secret_key).dumps({
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
        if not use_s3():
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
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': 'Not found'}), 404
        panorama, access_type = get_panorama_with_access(sb, panorama_id, user_id)
        if not panorama or not can_edit_plots(access_type):
            return jsonify({'error': 'Forbidden'}), 403
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not fully configured'}), 503
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
                raw = read_uploaded_file_bytes(file, MAX_PLOT_IMAGE_BYTES)
            except ValueError as ve:
                if str(ve) == 'file_too_large':
                    return jsonify({'error': f'Image too large (max {MAX_PLOT_IMAGE_BYTES // (1024*1024)}MB)'}), 413
                return jsonify({'error': 'Invalid upload'}), 400
            unique = uuid.uuid4().hex[:10]
            filename = f"plot_{plot_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{unique}.{ext}"
            content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
            try:
                client.put_object(
                    Bucket=SUPABASE_S3_BUCKET,
                    Key=plot_object_key(filename),
                    Body=raw,
                    ContentType=content_type,
                )
            except Exception as e:
                current_app.logger.exception('Direct plot image upload failed')
                return jsonify({'error': str(e) or 'Upload failed'}), 500
            try:
                try:
                    sb.table('plots').update({
                        'image_filename': filename,
                        'image_content_type': content_type,
                    }).eq('id', plot_id).execute()
                except Exception as ee:
                    if 'image_content_type' in str(ee).lower():
                        sb.table('plots').update({'image_filename': filename}).eq('id', plot_id).execute()
                    else:
                        raise
                sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
            except Exception as e:
                msg = str(e).lower()
                if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
                    return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
                return jsonify({'error': str(e)}), 500
            if old_filename and old_filename != filename:
                delete_plot_from_s3(old_filename)
            return jsonify({'success': True, 'filename': filename})
        payload = request.form or {}
        filename = os.path.basename(str(payload.get('filename') or '')).strip()
        upload_token = str(payload.get('upload_token') or '').strip()
        if not filename:
            return jsonify({'error': 'filename is required'}), 400
        if not upload_token:
            return jsonify({'error': 'upload_token is required'}), 400
        try:
            signed = plot_upload_serializer(secret_key).loads(upload_token, max_age=int(SUPABASE_S3_UPLOAD_URL_TTL))
        except SignatureExpired:
            return jsonify({'error': 'upload_token expired; request a new upload URL'}), 400
        except BadSignature:
            return jsonify({'error': 'Invalid upload_token'}), 400
        if str(signed.get('user_id')) != str(user_id) or int(signed.get('plot_id')) != int(plot_id) or str(signed.get('filename')) != filename:
            return jsonify({'error': 'Invalid upload_token'}), 403
        head = None
        for attempt in range(5):
            try:
                head = client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=plot_object_key(filename))
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
            delete_plot_from_s3(filename)
            return jsonify({'error': f'Image too large (max {MAX_PLOT_IMAGE_BYTES // (1024*1024)}MB)'}), 413
        try:
            try:
                sb.table('plots').update({
                    'image_filename': filename,
                    'image_content_type': str(signed.get('content_type') or ''),
                }).eq('id', plot_id).execute()
            except Exception as ee:
                if 'image_content_type' in str(ee).lower():
                    sb.table('plots').update({'image_filename': filename}).eq('id', plot_id).execute()
                else:
                    raise
            sb.table('panoramas').update({'updated_at': datetime.utcnow().isoformat()}).eq('id', panorama_id).execute()
        except Exception as e:
            msg = str(e).lower()
            if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': str(e)}), 500
        if old_filename and old_filename != filename:
            delete_plot_from_s3(old_filename)
        return jsonify({'success': True})

    @app.route('/api/markers/<marker_id>/image', methods=['GET'])
    @require_auth
    def get_marker_image(user_id, role, marker_id):
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
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not configured'}), 503
        use_thumb = request.args.get('size') == 'thumb'
        key = marker_object_key(image_filename)
        if use_thumb:
            thumb_key = marker_thumb_object_key(image_filename)
            if thumb_key:
                try:
                    obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=thumb_key)
                    body = obj.get('Body')
                    data = body.read() if body else b''
                    try:
                        if body:
                            body.close()
                    except Exception:
                        pass
                    content_type = obj.get('ContentType') or 'image/jpeg'
                    resp = Response(data, mimetype=str(content_type or 'image/jpeg'))
                    resp.headers['Cache-Control'] = 'private, max-age=900'
                    return resp
                except Exception:
                    pass
        try:
            obj = client.get_object(Bucket=SUPABASE_S3_BUCKET, Key=key)
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

    @app.route('/api/markers/<marker_id>/voiceover', methods=['GET'])
    @require_auth
    def get_marker_voiceover(user_id, role, marker_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            r = sb.table('plot_markers').select('id, plot_id, voiceover_filename').eq('id', marker_id).limit(1).execute()
            if not r.data:
                return jsonify({'error': 'Marker not found'}), 404
            row = r.data[0]
        except Exception as e:
            msg = str(e).lower()
            if 'voiceover_filename' in msg and ('does not exist' in msg or 'column' in msg):
                return jsonify({'error': 'voiceover_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
        voiceover_filename = row.get('voiceover_filename') or ''
        if not voiceover_filename:
            return jsonify({'error': 'No voice-over'}), 404
        url = get_voiceover_s3_url(voiceover_filename)
        if not url:
            return jsonify({'error': 'Voice-over not found'}), 404
        return jsonify({'url': url})

    @app.route('/api/markers/<marker_id>/voiceover', methods=['POST'])
    @require_auth
    def upload_marker_voiceover(user_id, role, marker_id):
        if not use_s3():
            return jsonify({'error': 'Supabase S3 is not configured'}), 503
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            r = sb.table('plot_markers').select('id, plot_id, voiceover_filename').eq('id', marker_id).limit(1).execute()
            if not r.data:
                return jsonify({'error': 'Marker not found'}), 404
            row = r.data[0]
        except Exception as e:
            msg = str(e).lower()
            if 'voiceover_filename' in msg and ('does not exist' in msg or 'column' in msg):
                return jsonify({'error': 'voiceover_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
        old_filename = str(row.get('voiceover_filename') or '').strip()
        file_storage = request.files.get('file') or request.files.get('voiceover')
        if not file_storage or not getattr(file_storage, 'filename', None):
            return jsonify({'error': 'No audio file (use form field "file" or "voiceover")'}), 400
        filename_orig = (file_storage.filename or '').strip()
        ext = (filename_orig.rsplit('.', 1)[-1].lower() if '.' in filename_orig else '').strip()
        if ext not in ALLOWED_AUDIO_EXTENSIONS:
            return jsonify({'error': 'Audio type not allowed. Use: mp3, wav, m4a, ogg, webm'}), 400
        data = read_uploaded_file_bytes(file_storage, MAX_MARKER_VOICEOVER_BYTES)
        if len(data) > MAX_MARKER_VOICEOVER_BYTES:
            return jsonify({'error': f'Voice-over too large (max {MAX_MARKER_VOICEOVER_BYTES // (1024*1024)}MB)'}), 413
        if not data:
            return jsonify({'error': 'Empty file'}), 400
        unique = uuid.uuid4().hex[:10]
        filename = f"voice_{marker_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{unique}.{ext}"
        content_type = AUDIO_CONTENT_TYPES.get(ext, 'audio/mpeg')
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not configured'}), 503
        try:
            client.put_object(
                Bucket=SUPABASE_S3_BUCKET,
                Key=voiceover_object_key(filename),
                Body=data,
                ContentType=content_type,
            )
        except Exception as e:
            current_app.logger.exception('Voice-over S3 upload failed')
            return jsonify({'error': str(e)}), 500
        try:
            sb.table('plot_markers').update({
                'voiceover_filename': filename,
                'updated_at': datetime.utcnow().isoformat(),
            }).eq('id', marker_id).execute()
        except Exception as e:
            current_app.logger.exception('Failed to update marker voiceover_filename')
            try:
                delete_voiceover_from_s3(filename)
            except Exception:
                pass
            return jsonify({'error': 'Failed to save'}), 500
        if old_filename and old_filename != filename:
            delete_voiceover_from_s3(old_filename)
        return jsonify({'success': True, 'voiceover_filename': filename})

    @app.route('/api/markers/<marker_id>/image/upload-url', methods=['POST'])
    @require_auth
    def create_marker_image_upload_url(user_id, role, marker_id):
        if not use_s3():
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
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not fully configured'}), 503
        try:
            upload_url = client.generate_presigned_url(
                ClientMethod='put_object',
                Params={'Bucket': SUPABASE_S3_BUCKET, 'Key': marker_object_key(filename), 'ContentType': content_type},
                ExpiresIn=int(SUPABASE_S3_UPLOAD_URL_TTL),
            )
        except Exception as e:
            current_app.logger.exception('Failed to generate marker image upload URL')
            return jsonify({'error': str(e)}), 500
        token = marker_upload_serializer(secret_key).dumps({
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
        if not use_s3():
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
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
        client = get_s3_client()
        if not client:
            return jsonify({'error': 'Supabase S3 is not fully configured'}), 503
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
                raw = read_uploaded_file_bytes(file, MAX_MARKER_IMAGE_BYTES)
            except ValueError as ve:
                if str(ve) == 'file_too_large':
                    return jsonify({'error': f'Image too large (max {MAX_MARKER_IMAGE_BYTES // (1024*1024)}MB)'}), 413
                return jsonify({'error': 'Invalid upload'}), 400
            unique = uuid.uuid4().hex[:10]
            compressed = compress_marker_image(raw)
            if compressed:
                full_bytes, thumb_bytes = compressed
                filename = f"marker_{marker_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{unique}.jpg"
                content_type = 'image/jpeg'
                body_to_upload = full_bytes
                thumb_key = marker_thumb_object_key(filename)
            else:
                filename = f"marker_{marker_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{unique}.{ext}"
                content_type = IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')
                body_to_upload = raw
                thumb_key = None
            try:
                client.put_object(
                    Bucket=SUPABASE_S3_BUCKET,
                    Key=marker_object_key(filename),
                    Body=body_to_upload,
                    ContentType=content_type,
                )
            except Exception as e:
                current_app.logger.exception('Direct marker image upload failed')
                return jsonify({'error': str(e) or 'Upload failed'}), 500
            if thumb_key and compressed:
                try:
                    client.put_object(
                        Bucket=SUPABASE_S3_BUCKET,
                        Key=thumb_key,
                        Body=thumb_bytes,
                        ContentType='image/jpeg',
                    )
                except Exception:
                    pass
            try:
                sb.table('plot_markers').update({'image_filename': filename}).eq('id', marker_id).execute()
            except Exception as e:
                msg = str(e).lower()
                if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
                    return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
                return jsonify({'error': str(e)}), 500
            if old_filename and old_filename != filename:
                delete_marker_from_s3(old_filename)
            return jsonify({'success': True, 'filename': filename})
        payload = request.form or {}
        filename = os.path.basename(str(payload.get('filename') or '')).strip()
        upload_token = str(payload.get('upload_token') or '').strip()
        if not filename:
            return jsonify({'error': 'filename is required'}), 400
        if not upload_token:
            return jsonify({'error': 'upload_token is required'}), 400
        try:
            signed = marker_upload_serializer(secret_key).loads(upload_token, max_age=int(SUPABASE_S3_UPLOAD_URL_TTL))
        except SignatureExpired:
            return jsonify({'error': 'upload_token expired; request a new upload URL'}), 400
        except BadSignature:
            return jsonify({'error': 'Invalid upload_token'}), 400
        if str(signed.get('user_id')) != str(user_id) or str(signed.get('marker_id')) != str(marker_id) or str(signed.get('filename')) != filename:
            return jsonify({'error': 'Invalid upload_token'}), 403
        head = None
        for attempt in range(5):
            try:
                head = client.head_object(Bucket=SUPABASE_S3_BUCKET, Key=marker_object_key(filename))
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
            delete_marker_from_s3(filename)
            return jsonify({'error': f'Image too large (max {MAX_MARKER_IMAGE_BYTES // (1024*1024)}MB)'}), 413
        try:
            sb.table('plot_markers').update({'image_filename': filename}).eq('id', marker_id).execute()
        except Exception as e:
            msg = str(e).lower()
            if 'image_filename' in msg and ('does not exist' in msg or 'column' in msg):
                return jsonify({'error': 'image_filename column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': str(e)}), 500
        if old_filename and old_filename != filename:
            delete_marker_from_s3(old_filename)
        return jsonify({'success': True})

    @app.route('/api/admin/users', methods=['POST'])
    @require_auth
    def admin_create_user(user_id, role):
        if role not in ('admin', 'superadmin'):
            return jsonify({'error': 'Only admins can create users'}), 403
        if not app_config.SUPABASE_URL or not app_config.SUPABASE_SERVICE_ROLE_KEY:
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
            sb = create_client(app_config.SUPABASE_URL, app_config.SUPABASE_SERVICE_ROLE_KEY)
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

    @app.route('/api/profile/me', methods=['PUT', 'PATCH'])
    @require_auth
    def update_my_profile(user_id, role):
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
        payload = {'updated_at': updated_at}
        if 'display_name' in data:
            payload['display_name'] = display_name
        if 'email' in data:
            payload['email'] = email
        try:
            if existing:
                sb.table('profiles').update(payload).eq('user_id', user_id).execute()
            else:
                full = {
                    'user_id': user_id,
                    'role': (existing.get('role') if existing else 'user'),
                    'updated_at': updated_at,
                }
                if 'display_name' in data:
                    full['display_name'] = display_name
                if 'email' in data:
                    full['email'] = email
                sb.table('profiles').upsert(full, on_conflict='user_id').execute()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/org/me', methods=['GET'])
    @require_auth
    def get_my_org(user_id, role):
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
                    return jsonify({'error': 'organizations table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            if org and org.get('name'):
                org['slug'] = slugify_org_name(org.get('name'))
        out_profile = {
            'user_id': str(profile.get('user_id') or user_id),
            'role': str(profile.get('role') or role or 'user'),
            'org_id': str(org_id) if org_id else None,
            'display_name': profile.get('display_name'),
            'email': profile.get('email'),
        }
        return jsonify({'profile': out_profile, 'org': org})

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
                return jsonify({'error': 'organizations table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
                return jsonify({'error': 'organizations table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
                return jsonify({'error': 'organizations table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
                return jsonify({'error': 'org_id column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            if 'organizations' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
                return jsonify({'error': 'organizations table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500
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
            try:
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
                return jsonify({'error': 'org_id column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

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
                    return jsonify({'error': 'org_id column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
                return jsonify({'error': 'org_id column missing. Run db/schema.sql in Supabase SQL Editor.'}), 503
            if 'role' in msg.lower() and 'check' in msg.lower():
                return jsonify({'error': 'profiles.role constraint does not allow this value. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    @app.route('/api/crm/panoramas', methods=['GET'])
    @require_auth
    def list_crm_panoramas(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama_ids = _crm_panorama_ids(sb, user_id, role)
        if not panorama_ids:
            return jsonify([])
        out_by_id = {}
        ws_ids_needed = set()
        chunk_size = 100
        try:
            for i in range(0, len(panorama_ids), chunk_size):
                chunk = panorama_ids[i:i + chunk_size]
                try:
                    r = sb.table('panoramas').select('id, name, workspace_id, created_at, updated_at').in_('id', chunk).execute()
                except Exception:
                    r = sb.table('panoramas').select('id, name, created_at, updated_at').in_('id', chunk).execute()
                for row in (r.data or []):
                    try:
                        pid = int(row.get('id'))
                    except Exception:
                        continue
                    wsid = row.get('workspace_id') or None
                    item = {
                        'id': pid,
                        'name': str(row.get('name') or f'Panorama #{pid}'),
                        'workspace_id': str(wsid) if wsid else None,
                        'created_at': str(row.get('created_at') or ''),
                        'updated_at': str(row.get('updated_at') or ''),
                    }
                    out_by_id[pid] = item
                    if wsid:
                        ws_ids_needed.add(str(wsid))
        except Exception as e:
            msg = str(e)
            if 'panoramas' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
                return jsonify({'error': 'panoramas table not found'}), 503
            return jsonify({'error': msg}), 500
        # Fetch workspace names for project grouping
        ws_names = {}
        if ws_ids_needed:
            try:
                ws_list = list(ws_ids_needed)
                for i in range(0, len(ws_list), chunk_size):
                    chunk = ws_list[i:i + chunk_size]
                    wr = sb.table('workspaces').select('id, name').in_('id', chunk).execute()
                    for row in (wr.data or []):
                        ws_names[str(row.get('id'))] = str(row.get('name') or '')
            except Exception:
                pass
        for item in out_by_id.values():
            wsid = item.get('workspace_id')
            item['workspace_name'] = ws_names.get(wsid, '') if wsid else ''
        out = list(out_by_id.values())
        out.sort(key=lambda x: (x.get('updated_at') or '', x.get('name') or ''), reverse=True)
        return jsonify(out)

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
                return jsonify({'error': 'buy_interests table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
                return jsonify({'error': 'buy_interests table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    @app.route('/api/buy-interests', methods=['GET'])
    @require_auth
    def list_buy_interests(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama_ids = _crm_panorama_ids(sb, user_id, role)
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
                return jsonify({'error': 'buy_interests table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    @app.route('/api/buy-interests/<interest_id>', methods=['PUT', 'PATCH'])
    @require_auth
    def update_buy_interest(user_id, role, interest_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama_ids = _crm_panorama_ids(sb, user_id, role)
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
                return jsonify({'error': 'buy_interests table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
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
                return jsonify({'error': 'buy_interests table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    # ----- CRM extended endpoints -----

    @app.route('/api/crm/me', methods=['GET'])
    @require_auth
    def crm_me(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        profile = get_profile(sb, user_id) or {}
        has_crm = False
        try:
            panorama_ids = _crm_panorama_ids(sb, user_id, role)
            has_crm = len(panorama_ids) > 0
        except Exception:
            # For admin/superadmin, assume CRM access even if query fails
            normalized = str(role or '').strip().lower()
            has_crm = normalized in ('admin', 'superadmin')
        return jsonify({
            'user_id': user_id,
            'role': role,
            'org_id': profile.get('org_id'),
            'display_name': profile.get('display_name') or profile.get('email') or '',
            'has_crm_access': has_crm,
        })

    @app.route('/api/crm/plots', methods=['GET'])
    @require_auth
    def list_crm_all_plots(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        pano_ids = _crm_panorama_ids(sb, user_id, role)
        if not pano_ids:
            return jsonify([])
        pano_names = {}
        all_plots = []
        for i in range(0, len(pano_ids), 100):
            chunk = pano_ids[i:i+100]
            try:
                panos_r = sb.table('panoramas').select('id, name').in_('id', chunk).execute()
                for p in (panos_r.data or []):
                    pano_names[p['id']] = p.get('name') or ('Project #' + str(p['id']))
            except Exception:
                pass
            try:
                plots_r = sb.table('plots').select('id, panorama_id, name, area, price, status, description').in_('panorama_id', chunk).execute()
                all_plots.extend(plots_r.data or [])
            except Exception:
                pass
        for p in all_plots:
            p['panorama_name'] = pano_names.get(p.get('panorama_id'), '')
        return jsonify(all_plots)

    @app.route('/api/crm/panoramas/<int:panorama_id>/markers', methods=['GET'])
    @require_auth
    def list_crm_panorama_markers(user_id, role, panorama_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        pano_ids = _crm_panorama_ids(sb, user_id, role)
        if panorama_id not in pano_ids:
            return jsonify({'error': 'Not authorized for this panorama'}), 403
        try:
            r = sb.table('plot_markers').select('id, plot_id, name, description, status, marker_style, marker_icon, marker_color, rotation_x, rotation_y, rotation_z, longitude, latitude, linked_panorama_id, created_at').eq('plot_id', str(panorama_id)).execute()
            return jsonify(r.data or [])
        except Exception as e:
            msg = str(e)
            if 'plot_markers' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
                return jsonify([])
            return jsonify({'error': msg}), 500

    @app.route('/api/crm/markers/<marker_id>', methods=['PUT', 'PATCH'])
    @require_auth
    def update_crm_marker(user_id, role, marker_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        data = request.get_json(silent=True) or {}
        try:
            r = sb.table('plot_markers').select('plot_id').eq('id', str(marker_id)).limit(1).execute()
            if not r.data:
                return jsonify({'error': 'Marker not found'}), 404
            panorama_id = int(r.data[0].get('plot_id', 0))
        except Exception:
            return jsonify({'error': 'Marker not found'}), 404
        pano_ids = _crm_panorama_ids(sb, user_id, role)
        if panorama_id not in pano_ids:
            return jsonify({'error': 'Not authorized'}), 403
        allowed = {'name', 'description', 'status', 'marker_icon', 'marker_color', 'rotation_x', 'rotation_y', 'rotation_z'}
        upd = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not upd:
            return jsonify({'error': 'No valid fields to update'}), 400
        try:
            sb.table('plot_markers').update(upd).eq('id', str(marker_id)).execute()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/crm/org-users', methods=['GET'])
    @require_admin
    def list_crm_org_users(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        profile = get_profile(sb, user_id) or {}
        org_id = profile.get('org_id')
        if not org_id:
            return jsonify([])
        try:
            r = sb.table('profiles').select('user_id, display_name, email, role').eq('org_id', org_id).execute()
            # Only return rows that have a valid user_id — orphaned or incomplete profiles are excluded
            users = [row for row in (r.data or []) if row.get('user_id') and str(row['user_id']).strip()]
            return jsonify(users)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/crm/lock-access', methods=['GET'])
    @require_admin
    def list_lock_access(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama_id = request.args.get('panorama_id')
        if not panorama_id:
            return jsonify({'error': 'panorama_id required'}), 400
        try:
            panorama_id = int(panorama_id)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid panorama_id'}), 400
        try:
            r = sb.table('plot_lock_access').select('id, panorama_id, user_id, granted_by, created_at').eq('panorama_id', panorama_id).execute()
            return jsonify(r.data or [])
        except Exception as e:
            msg = str(e)
            if 'plot_lock_access' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
                return jsonify({'error': 'plot_lock_access table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    @app.route('/api/crm/lock-access', methods=['POST'])
    @require_admin
    def grant_lock_access(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        data = request.get_json(silent=True) or {}
        try:
            panorama_id = int(data.get('panorama_id'))
        except (ValueError, TypeError):
            return jsonify({'error': 'panorama_id required'}), 400
        target_user_id = str(data.get('user_id') or '').strip()
        if not target_user_id:
            return jsonify({'error': 'user_id required'}), 400
        profile = get_profile(sb, user_id) or {}
        org_id = profile.get('org_id')
        try:
            sb.table('plot_lock_access').upsert({
                'panorama_id': panorama_id,
                'user_id': target_user_id,
                'granted_by': user_id,
                'org_id': org_id,
            }, on_conflict='panorama_id,user_id').execute()
            return jsonify({'success': True}), 201
        except Exception as e:
            msg = str(e)
            if 'plot_lock_access' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
                return jsonify({'error': 'plot_lock_access table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    @app.route('/api/crm/lock-access', methods=['DELETE'])
    @require_admin
    def revoke_lock_access(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        data = request.get_json(silent=True) or {}
        try:
            panorama_id = int(data.get('panorama_id'))
        except (ValueError, TypeError):
            return jsonify({'error': 'panorama_id required'}), 400
        target_user_id = str(data.get('user_id') or '').strip()
        if not target_user_id:
            return jsonify({'error': 'user_id required'}), 400
        try:
            sb.table('plot_lock_access').delete().eq('panorama_id', panorama_id).eq('user_id', target_user_id).execute()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/crm/lockable-plots', methods=['GET'])
    @require_auth
    def list_lockable_plots(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            r = sb.table('plot_lock_access').select('panorama_id').eq('user_id', user_id).execute()
            panorama_ids = [row['panorama_id'] for row in (r.data or [])]
        except Exception:
            panorama_ids = []
        if not panorama_ids:
            return jsonify([])
        all_plots = []
        pano_names = {}
        for i in range(0, len(panorama_ids), 100):
            chunk = panorama_ids[i:i+100]
            try:
                panos_r = sb.table('panoramas').select('id, name').in_('id', chunk).execute()
                for p in (panos_r.data or []):
                    pano_names[p['id']] = p.get('name') or ('Project #' + str(p['id']))
            except Exception:
                pass
            try:
                plots_r = sb.table('plots').select('id, panorama_id, name, area, price, status').in_('panorama_id', chunk).execute()
                all_plots.extend(plots_r.data or [])
            except Exception:
                pass
        locks_by_plot = {}
        plot_ids = [p['id'] for p in all_plots]
        if plot_ids:
            for i in range(0, len(plot_ids), 100):
                chunk = plot_ids[i:i+100]
                try:
                    locks_r = sb.table('plot_locks').select('*').in_('plot_id', chunk).execute()
                    for lock in (locks_r.data or []):
                        locks_by_plot[lock['plot_id']] = lock
                except Exception:
                    pass
        result = []
        for plot in all_plots:
            lock = locks_by_plot.get(plot['id'])
            result.append({
                **plot,
                'panorama_name': pano_names.get(plot.get('panorama_id'), ''),
                'lock': lock,
            })
        return jsonify(result)

    @app.route('/api/crm/plots/<int:plot_id>/lock', methods=['POST'])
    @require_auth
    def lock_plot(user_id, role, plot_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            plot_r = sb.table('plots').select('panorama_id').eq('id', plot_id).limit(1).execute()
            if not plot_r.data:
                return jsonify({'error': 'Plot not found'}), 404
            panorama_id = plot_r.data[0]['panorama_id']
        except Exception:
            return jsonify({'error': 'Plot not found'}), 404
        try:
            access_r = sb.table('plot_lock_access').select('id').eq('panorama_id', panorama_id).eq('user_id', user_id).limit(1).execute()
            if not access_r.data:
                return jsonify({'error': 'No lock access for this panorama'}), 403
        except Exception:
            return jsonify({'error': 'No lock access for this panorama'}), 403
        data = request.get_json(silent=True) or {}
        lock_row = {
            'plot_id': plot_id,
            'locked_by': user_id,
            'locked_for_name': str(data.get('locked_for_name') or '').strip() or None,
            'locked_for_email': str(data.get('locked_for_email') or '').strip() or None,
        }
        try:
            sb.table('plot_locks').upsert(lock_row, on_conflict='plot_id').execute()
            return jsonify({'success': True}), 201
        except Exception as e:
            msg = str(e)
            if 'plot_locks' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
                return jsonify({'error': 'plot_locks table not found. Run db/schema.sql in Supabase SQL Editor.'}), 503
            return jsonify({'error': msg}), 500

    @app.route('/api/crm/plots/<int:plot_id>/lock', methods=['DELETE'])
    @require_auth
    def unlock_plot(user_id, role, plot_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        try:
            lock_r = sb.table('plot_locks').select('id, locked_by').eq('plot_id', plot_id).limit(1).execute()
            if not lock_r.data:
                return jsonify({'error': 'Plot is not locked'}), 404
            if str(lock_r.data[0].get('locked_by')) != str(user_id) and role not in ('admin', 'superadmin'):
                return jsonify({'error': 'Only the user who locked this plot or an admin can unlock it'}), 403
            sb.table('plot_locks').delete().eq('plot_id', plot_id).execute()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ------------------------------------------------------------------
    # Day Night Projects
    # ------------------------------------------------------------------

    @app.route('/daynight')
    def daynight_page():
        return render_template('daynight.html', **auth_ctx())

    @app.route('/api/daynight', methods=['GET'])
    @require_admin
    def api_list_daynight(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        projects = dn_list(sb, user_id)
        for p in projects:
            p['share_url'] = f"{(request.url_root or '').rstrip('/')}/daynight/view/{p.get('share_token', '')}"
            # Include media_url for thumbnails
            if p.get('media_type') == 'image' and p.get('stitched_filename'):
                p['media_url'] = get_daynight_s3_url(p['stitched_filename'])
            elif p.get('media_type') == 'video' and p.get('video_filename'):
                p['media_url'] = get_daynight_s3_url(p['video_filename'])
            else:
                p['media_url'] = None
            # Include preview URL
            p['preview_url'] = f"{(request.url_root or '').rstrip('/')}/daynight/preview/{p.get('share_token', '')}"
        return jsonify(projects)

    @app.route('/api/daynight', methods=['POST'])
    @require_admin
    def api_create_daynight(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or '').strip()
        media_type = str(data.get('media_type') or '').strip()
        if not name:
            return jsonify({'error': 'Name is required'}), 400
        if media_type not in ('image', 'video'):
            return jsonify({'error': 'media_type must be image or video'}), 400
        profile = get_profile(sb, user_id)
        org_id = profile.get('org_id') if profile else None
        project = dn_create(sb, user_id, org_id, name, media_type)
        if not project:
            return jsonify({'error': 'Failed to create project'}), 500
        project['share_url'] = f"{(request.url_root or '').rstrip('/')}/daynight/view/{project.get('share_token', '')}"
        return jsonify(project), 201

    @app.route('/api/daynight/<project_id>', methods=['GET'])
    @require_admin
    def api_get_daynight(user_id, role, project_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        project = dn_get(sb, project_id)
        if not project:
            return jsonify({'error': 'Not found'}), 404
        if str(project.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        media_url = None
        if project.get('media_type') == 'image' and project.get('stitched_filename'):
            media_url = get_daynight_s3_url(project['stitched_filename'])
        elif project.get('media_type') == 'video' and project.get('video_filename'):
            media_url = get_daynight_s3_url(project['video_filename'])
        project['media_url'] = media_url
        project['share_url'] = f"{(request.url_root or '').rstrip('/')}/daynight/view/{project.get('share_token', '')}"
        return jsonify(project)

    @app.route('/api/daynight/<project_id>/upload-images', methods=['POST'])
    @require_admin
    def api_daynight_upload_images(user_id, role, project_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        project = dn_get(sb, project_id)
        if not project:
            return jsonify({'error': 'Not found'}), 404
        if str(project.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        if project.get('media_type') != 'image':
            return jsonify({'error': 'Project media_type is not image'}), 400
        files = request.files.getlist('images')
        if not files or len(files) < 2:
            return jsonify({'error': 'At least 2 images required'}), 400
        image_bytes_list = []
        source_names = []
        for f in files:
            ext = (f.filename or '').rsplit('.', 1)[-1].lower() if f.filename else ''
            if ext not in ALLOWED_EXTENSIONS:
                return jsonify({'error': f'Invalid image type: {f.filename}'}), 400
            raw = read_uploaded_file_bytes(f, MAX_DAYNIGHT_IMAGE_READ_BYTES)
            if not raw:
                return jsonify({'error': f'Empty file: {f.filename}'}), 400
            image_bytes_list.append(raw)
            source_names.append(f.filename or 'unknown')
        try:
            stitched_bytes, join_positions, width, height = stitch_images_horizontally(image_bytes_list)
        except Exception as e:
            return jsonify({'error': f'Stitching failed: {str(e)}'}), 500
        filename = f"dn_{project_id}_{uuid.uuid4().hex[:8]}.jpg"
        # Delete old stitched image if exists
        old_fn = project.get('stitched_filename')
        if old_fn:
            delete_daynight_from_s3(old_fn)
        try:
            upload_daynight_to_s3(filename, stitched_bytes, 'image/jpeg')
        except Exception as e:
            return jsonify({'error': f'Upload failed: {str(e)}'}), 500
        dn_update(sb, project_id,
                  stitched_filename=filename,
                  stitched_width=width,
                  stitched_height=height,
                  join_positions=join_positions,
                  source_images=source_names)
        updated = dn_get(sb, project_id)
        if updated:
            updated['media_url'] = get_daynight_s3_url(filename)
            updated['share_url'] = f"{(request.url_root or '').rstrip('/')}/daynight/view/{updated.get('share_token', '')}"
        return jsonify(updated or {'success': True})

    @app.route('/api/daynight/<project_id>/upload-video', methods=['POST'])
    @require_admin
    def api_daynight_upload_video(user_id, role, project_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        project = dn_get(sb, project_id)
        if not project:
            return jsonify({'error': 'Not found'}), 404
        if str(project.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        if project.get('media_type') != 'video':
            return jsonify({'error': 'Project media_type is not video'}), 400
        f = request.files.get('video')
        if not f:
            return jsonify({'error': 'No video file provided'}), 400
        ext = (f.filename or '').rsplit('.', 1)[-1].lower() if f.filename else ''
        if ext not in ALLOWED_VIDEO_EXTENSIONS:
            return jsonify({'error': f'Invalid video type. Allowed: {", ".join(ALLOWED_VIDEO_EXTENSIONS)}'}), 400
        content_type = VIDEO_CONTENT_TYPES.get(ext, 'video/mp4')
        raw = read_uploaded_file_bytes(f, MAX_DAYNIGHT_VIDEO_BYTES)
        if not raw:
            return jsonify({'error': 'Empty file'}), 400
        filename = f"dn_{project_id}_{uuid.uuid4().hex[:8]}.{ext}"
        old_fn = project.get('video_filename')
        if old_fn:
            delete_daynight_from_s3(old_fn)
        try:
            upload_daynight_to_s3(filename, raw, content_type)
        except Exception as e:
            return jsonify({'error': f'Upload failed: {str(e)}'}), 500
        dn_update(sb, project_id, video_filename=filename)
        updated = dn_get(sb, project_id)
        if updated:
            updated['media_url'] = get_daynight_s3_url(filename)
            updated['share_url'] = f"{(request.url_root or '').rstrip('/')}/daynight/view/{updated.get('share_token', '')}"
        return jsonify(updated or {'success': True})

    @app.route('/api/daynight/<project_id>', methods=['DELETE'])
    @require_admin
    def api_delete_daynight(user_id, role, project_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        project = dn_get(sb, project_id)
        if not project:
            return jsonify({'error': 'Not found'}), 404
        if str(project.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        if project.get('stitched_filename'):
            delete_daynight_from_s3(project['stitched_filename'])
        if project.get('video_filename'):
            delete_daynight_from_s3(project['video_filename'])
        dn_delete(sb, project_id)
        return jsonify({'success': True})

    # Admin preview page for Day Night projects (with speed controls)
    @app.route('/daynight/preview/<share_token>')
    def daynight_preview_page(share_token):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        project = dn_get_by_token(sb, share_token)
        if not project:
            return "Not found", 404
        media_url = None
        if project.get('media_type') == 'image' and project.get('stitched_filename'):
            media_url = get_daynight_s3_url(project['stitched_filename'])
        elif project.get('media_type') == 'video' and project.get('video_filename'):
            media_url = get_daynight_s3_url(project['video_filename'])
        if not media_url:
            return "Media not yet uploaded", 404
        return render_template('daynight_preview.html',
                               project=project,
                               media_url=media_url,
                               media_type=project.get('media_type'),
                               stitched_width=project.get('stitched_width', 0),
                               stitched_height=project.get('stitched_height', 0),
                               drag_speed=project.get('drag_speed') or 80,
                               **auth_ctx())

    @app.route('/api/daynight/<project_id>/settings', methods=['POST'])
    @require_admin
    def api_daynight_save_settings(user_id, role, project_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        project = dn_get(sb, project_id)
        if not project:
            return jsonify({'error': 'Not found'}), 404
        if str(project.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        drag_speed = data.get('drag_speed')
        if drag_speed is not None:
            try:
                drag_speed = float(drag_speed)
                if drag_speed < 10 or drag_speed > 500:
                    return jsonify({'error': 'drag_speed must be between 10 and 500'}), 400
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid drag_speed'}), 400
            dn_update(sb, project_id, drag_speed=drag_speed)
        updated = dn_get(sb, project_id)
        return jsonify(updated or {'success': True})

    # Public customer view for Day Night projects
    @app.route('/daynight/view/<share_token>')
    def daynight_customer_view(share_token):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        project = dn_get_by_token(sb, share_token)
        if not project:
            return "Not found", 404
        media_url = None
        if project.get('media_type') == 'image' and project.get('stitched_filename'):
            media_url = get_daynight_s3_url(project['stitched_filename'])
        elif project.get('media_type') == 'video' and project.get('video_filename'):
            media_url = get_daynight_s3_url(project['video_filename'])
        if not media_url:
            return "Media not yet uploaded", 404
        return render_template('daynight_view.html',
                               project=project,
                               media_url=media_url,
                               media_type=project.get('media_type'),
                               stitched_width=project.get('stitched_width', 0),
                               stitched_height=project.get('stitched_height', 0),
                               drag_speed=project.get('drag_speed') or 80)

    # ==================================================================
    # Floor Plan Catalogue
    # ==================================================================

    @app.route('/floorplans')
    def floorplans_page():
        return render_template('floorplans.html', **auth_ctx())

    @app.route('/api/floorplans/catalogues', methods=['GET'])
    @require_admin
    def api_list_fp_catalogues(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        catalogues = fp_list_catalogues(sb, user_id)
        base = (request.url_root or '').rstrip('/')
        for c in catalogues:
            c['share_url'] = f"{base}/floorplans/view/{c.get('share_token', '')}"
            items = fp_list_items(sb, c['id'])
            c['item_count'] = len(items)
            c['item_names'] = [it.get('name', '') for it in items[:5]]
        return jsonify(catalogues)

    @app.route('/api/floorplans/catalogues', methods=['POST'])
    @require_admin
    def api_create_fp_catalogue(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or 'Floor Plans').strip()
        existing = fp_list_catalogues(sb, user_id)
        if any(c.get('name', '').strip().lower() == name.lower() for c in existing):
            return jsonify({'error': f'A catalogue named "{name}" already exists'}), 409
        profile = get_profile(sb, user_id)
        org_id = profile.get('org_id') if profile else None
        cat = fp_create_catalogue(sb, user_id, org_id, name)
        if not cat:
            return jsonify({'error': 'Failed to create catalogue'}), 500
        base = (request.url_root or '').rstrip('/')
        cat['share_url'] = f"{base}/floorplans/view/{cat.get('share_token', '')}"
        return jsonify(cat), 201

    @app.route('/api/floorplans/catalogues/<catalogue_id>', methods=['GET'])
    @require_admin
    def api_get_fp_catalogue(user_id, role, catalogue_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        cat = fp_get_catalogue(sb, catalogue_id)
        if not cat:
            return jsonify({'error': 'Not found'}), 404
        if str(cat.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        items = fp_list_items(sb, catalogue_id)
        for item in items:
            item['image_url'] = get_floorplan_s3_url(item.get('image_filename'))
        cat['items'] = items
        base = (request.url_root or '').rstrip('/')
        cat['share_url'] = f"{base}/floorplans/view/{cat.get('share_token', '')}"
        return jsonify(cat)

    @app.route('/api/floorplans/catalogues/<catalogue_id>', methods=['PATCH'])
    @require_admin
    def api_update_fp_catalogue(user_id, role, catalogue_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        cat = fp_get_catalogue(sb, catalogue_id)
        if not cat:
            return jsonify({'error': 'Not found'}), 404
        if str(cat.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or '').strip()
        if name:
            fp_update_catalogue(sb, catalogue_id, name=name)
        updated = fp_get_catalogue(sb, catalogue_id)
        return jsonify(updated)

    @app.route('/api/floorplans/catalogues/<catalogue_id>', methods=['DELETE'])
    @require_admin
    def api_delete_fp_catalogue(user_id, role, catalogue_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        cat = fp_get_catalogue(sb, catalogue_id)
        if not cat:
            return jsonify({'error': 'Not found'}), 404
        if str(cat.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        # Delete all item images from S3
        items = fp_list_items(sb, catalogue_id)
        for item in items:
            delete_floorplan_from_s3(item.get('image_filename'))
        fp_delete_catalogue(sb, catalogue_id)
        return jsonify({'success': True})

    # -- Floor Plan Items --

    @app.route('/api/floorplans/catalogues/<catalogue_id>/items', methods=['GET'])
    @require_admin
    def api_list_fp_items(user_id, role, catalogue_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        cat = fp_get_catalogue(sb, catalogue_id)
        if not cat:
            return jsonify({'error': 'Catalogue not found'}), 404
        if str(cat.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        items = fp_list_items(sb, catalogue_id)
        for item in items:
            item['image_url'] = get_floorplan_s3_url(item.get('image_filename'))
        return jsonify(items)

    @app.route('/api/floorplans/catalogues/<catalogue_id>/items', methods=['POST'])
    @require_admin
    def api_upload_fp_item(user_id, role, catalogue_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        cat = fp_get_catalogue(sb, catalogue_id)
        if not cat:
            return jsonify({'error': 'Catalogue not found'}), 404
        if str(cat.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        name = str(request.form.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Name is required'}), 400
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        if not allowed_file(f.filename):
            return jsonify({'error': 'Invalid file type'}), 400
        raw_bytes = f.read()
        if not raw_bytes or len(raw_bytes) > MAX_FLOORPLAN_READ_BYTES:
            return jsonify({'error': 'File too large'}), 400
        # Convert to WebP lossless (preserves transparency)
        webp_bytes, w, h = convert_floorplan_to_webp_lossless(raw_bytes)
        if not webp_bytes:
            return jsonify({'error': 'Failed to process image'}), 400
        # Generate unique filename
        filename = f"fp_{uuid.uuid4().hex[:16]}.webp"
        upload_floorplan_to_s3(filename, webp_bytes, 'image/webp')
        sort_order = fp_next_sort_order(sb, catalogue_id)
        item = fp_create_item(
            sb, catalogue_id, name, filename,
            image_width=w, image_height=h,
            file_size_bytes=len(webp_bytes),
            sort_order=sort_order,
        )
        if not item:
            return jsonify({'error': 'Failed to create item'}), 500
        item['image_url'] = get_floorplan_s3_url(filename)
        return jsonify(item), 201

    @app.route('/api/floorplans/items/<item_id>', methods=['PATCH'])
    @require_admin
    def api_update_fp_item(user_id, role, item_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        item = fp_get_item(sb, item_id)
        if not item:
            return jsonify({'error': 'Not found'}), 404
        cat = fp_get_catalogue(sb, item['catalogue_id'])
        if not cat or (str(cat.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        updates = {}
        if 'name' in data:
            updates['name'] = str(data['name']).strip()
        if updates:
            fp_update_item(sb, item_id, **updates)
        updated = fp_get_item(sb, item_id)
        if updated:
            updated['image_url'] = get_floorplan_s3_url(updated.get('image_filename'))
        return jsonify(updated)

    @app.route('/api/floorplans/items/<item_id>', methods=['DELETE'])
    @require_admin
    def api_delete_fp_item(user_id, role, item_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        item = fp_get_item(sb, item_id)
        if not item:
            return jsonify({'error': 'Not found'}), 404
        cat = fp_get_catalogue(sb, item['catalogue_id'])
        if not cat or (str(cat.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        delete_floorplan_from_s3(item.get('image_filename'))
        fp_delete_item(sb, item_id)
        return jsonify({'success': True})

    @app.route('/api/floorplans/catalogues/<catalogue_id>/reorder', methods=['POST'])
    @require_admin
    def api_reorder_fp_items(user_id, role, catalogue_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        cat = fp_get_catalogue(sb, catalogue_id)
        if not cat:
            return jsonify({'error': 'Catalogue not found'}), 404
        if str(cat.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        ordered_ids = data.get('ordered_ids', [])
        if not ordered_ids or not isinstance(ordered_ids, list):
            return jsonify({'error': 'ordered_ids must be a non-empty list'}), 400
        fp_reorder_items(sb, catalogue_id, ordered_ids)
        items = fp_list_items(sb, catalogue_id)
        for item in items:
            item['image_url'] = get_floorplan_s3_url(item.get('image_filename'))
        return jsonify(items)

    # -- Public Floor Plan View --

    @app.route('/floorplans/view/<share_token>')
    def floorplans_public_view(share_token):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        cat = fp_get_catalogue_by_token(sb, share_token)
        if not cat:
            return "Not found", 404
        items = fp_list_items(sb, cat['id'])
        for item in items:
            item['image_url'] = get_floorplan_s3_url(item.get('image_filename'))
        # Remove items with no image URL
        items = [it for it in items if it.get('image_url')]
        if not items:
            return "No floor plans uploaded yet", 404
        return render_template('floorplans_view.html',
                               catalogue=cat,
                               items=items,
                               workspace_name=cat.get('name', 'Floor Plans'))

    # ==================================================================
    # Building Maps
    # ==================================================================

    @app.route('/api/building-maps', methods=['GET'])
    @require_admin
    def api_list_building_maps(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        maps = bm_list(sb, user_id)
        base = (request.url_root or '').rstrip('/')
        for m in maps:
            m['image_url'] = get_building_map_s3_url(m.get('image_filename'))
            m['share_url'] = f"{base}/building-map/view/{m.get('share_token', '')}"
            zones = bm_list_zones(sb, m['id'])
            m['zone_count'] = len(zones)
            m['floor_count'] = len(set(z.get('floor_number', 0) for z in zones))
            if m.get('catalogue_id'):
                cat = fp_get_catalogue(sb, m['catalogue_id'])
                m['catalogue_name'] = cat.get('name', '') if cat else ''
            else:
                m['catalogue_name'] = ''
        return jsonify(maps)

    @app.route('/api/building-maps', methods=['POST'])
    @require_admin
    def api_create_building_map(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        name = str(request.form.get('name') or 'Building Map').strip()
        existing = bm_list(sb, user_id)
        if any(m.get('name', '').strip().lower() == name.lower() for m in existing):
            return jsonify({'error': f'A building map named "{name}" already exists'}), 409
        catalogue_id = str(request.form.get('catalogue_id') or '').strip() or None
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        if not allowed_file(f.filename):
            return jsonify({'error': 'Invalid file type'}), 400
        raw_bytes = f.read()
        if not raw_bytes or len(raw_bytes) > MAX_BUILDING_MAP_READ_BYTES:
            return jsonify({'error': 'File too large'}), 400
        jpeg_bytes, w, h = compress_building_map_image(raw_bytes)
        if not jpeg_bytes:
            return jsonify({'error': 'Failed to process image'}), 400
        filename = f"bm_{uuid.uuid4().hex[:16]}.jpg"
        upload_building_map_to_s3(filename, jpeg_bytes, 'image/jpeg')
        profile = get_profile(sb, user_id)
        org_id = profile.get('org_id') if profile else None
        bm = bm_create(sb, user_id, org_id, name, filename, w, h, catalogue_id)
        if not bm:
            return jsonify({'error': 'Failed to create building map'}), 500
        bm['image_url'] = get_building_map_s3_url(filename)
        base = (request.url_root or '').rstrip('/')
        bm['share_url'] = f"{base}/building-map/view/{bm.get('share_token', '')}"
        return jsonify(bm), 201

    @app.route('/api/building-maps/<map_id>', methods=['GET'])
    @require_admin
    def api_get_building_map(user_id, role, map_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get(sb, map_id)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        if str(bm.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        bm['image_url'] = get_building_map_s3_url(bm.get('image_filename'))
        zones = bm_list_zones(sb, map_id)
        bm['zones'] = zones
        base = (request.url_root or '').rstrip('/')
        bm['share_url'] = f"{base}/building-map/view/{bm.get('share_token', '')}"
        return jsonify(bm)

    @app.route('/api/building-maps/<map_id>', methods=['PATCH'])
    @require_admin
    def api_update_building_map(user_id, role, map_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get(sb, map_id)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        if str(bm.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        updates = {}
        if 'name' in data:
            updates['name'] = str(data['name']).strip()
        if 'catalogue_id' in data:
            updates['catalogue_id'] = str(data['catalogue_id']).strip() or None
        if updates:
            bm_update(sb, map_id, **updates)
        updated = bm_get(sb, map_id)
        if updated:
            updated['image_url'] = get_building_map_s3_url(updated.get('image_filename'))
        return jsonify(updated)

    @app.route('/api/building-maps/<map_id>', methods=['DELETE'])
    @require_admin
    def api_delete_building_map(user_id, role, map_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get(sb, map_id)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        if str(bm.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        delete_building_map_from_s3(bm.get('image_filename'))
        bm_delete(sb, map_id)
        return jsonify({'success': True})

    # -- Building Map Images --

    @app.route('/api/building-maps/<map_id>/images', methods=['GET'])
    @require_admin
    def api_list_building_map_images(user_id, role, map_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get(sb, map_id)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        if str(bm.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        images = bm_list_images(sb, map_id)
        for img in images:
            img['image_url'] = get_building_map_s3_url(img.get('image_filename'))
            img['zone_count'] = len(bm_list_zones_for_image(sb, img['id']))
        return jsonify(images)

    @app.route('/api/building-maps/<map_id>/images', methods=['POST'])
    @require_admin
    def api_create_building_map_image(user_id, role, map_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get(sb, map_id)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        if str(bm.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        name = str(request.form.get('name') or 'View').strip()
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        if not allowed_file(f.filename):
            return jsonify({'error': 'Invalid file type'}), 400
        raw_bytes = f.read()
        if not raw_bytes or len(raw_bytes) > MAX_BUILDING_MAP_READ_BYTES:
            return jsonify({'error': 'File too large'}), 400
        jpeg_bytes, w, h = compress_building_map_image(raw_bytes)
        if not jpeg_bytes:
            return jsonify({'error': 'Failed to process image'}), 400
        filename = f"bmi_{uuid.uuid4().hex[:16]}.jpg"
        upload_building_map_to_s3(filename, jpeg_bytes, 'image/jpeg')
        sort_order = bm_next_image_sort(sb, map_id)
        img = bm_create_image(sb, map_id, name, filename, w, h, sort_order)
        if not img:
            return jsonify({'error': 'Failed to create image'}), 500
        img['image_url'] = get_building_map_s3_url(filename)
        return jsonify(img), 201

    @app.route('/api/building-map-images/<image_id>', methods=['PATCH'])
    @require_admin
    def api_update_building_map_image(user_id, role, image_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        img = bm_get_image(sb, image_id)
        if not img:
            return jsonify({'error': 'Not found'}), 404
        bm = bm_get(sb, img['building_map_id'])
        if not bm or (str(bm.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        updates = {}
        if 'name' in data:
            updates['name'] = str(data['name']).strip()
        if 'sort_order' in data:
            updates['sort_order'] = int(data['sort_order'])
        if updates:
            bm_update_image(sb, image_id, **updates)
        updated = bm_get_image(sb, image_id)
        if updated:
            updated['image_url'] = get_building_map_s3_url(updated.get('image_filename'))
        return jsonify(updated)

    @app.route('/api/building-map-images/<image_id>', methods=['DELETE'])
    @require_admin
    def api_delete_building_map_image(user_id, role, image_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        img = bm_get_image(sb, image_id)
        if not img:
            return jsonify({'error': 'Not found'}), 404
        bm = bm_get(sb, img['building_map_id'])
        if not bm or (str(bm.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        delete_building_map_from_s3(img.get('image_filename'))
        bm_delete_image(sb, image_id)
        return jsonify({'success': True})

    # -- Building Zones --

    @app.route('/api/building-maps/<map_id>/zones', methods=['GET'])
    @require_admin
    def api_list_building_zones(user_id, role, map_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get(sb, map_id)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        if str(bm.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        zones = bm_list_zones(sb, map_id)
        return jsonify(zones)

    @app.route('/api/building-maps/<map_id>/zones', methods=['POST'])
    @require_admin
    def api_create_building_zone(user_id, role, map_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get(sb, map_id)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        if str(bm.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Name is required'}), 400
        points = data.get('points', [])
        if not isinstance(points, list) or len(points) < 3:
            return jsonify({'error': 'At least 3 points required'}), 400
        floor_number = int(data.get('floor_number', 0))
        color = str(data.get('color', 'green')).strip()
        linked_panorama_id = data.get('linked_panorama_id') or None
        linked_floor_plan_item_id = data.get('linked_floor_plan_item_id') or None
        sort_order = int(data.get('sort_order', 0))
        image_id = data.get('image_id') or None
        zone = bm_create_zone(sb, map_id, name, floor_number, points, color,
                              linked_panorama_id, linked_floor_plan_item_id, sort_order,
                              image_id=image_id)
        if not zone:
            return jsonify({'error': 'Failed to create zone'}), 500
        return jsonify(zone), 201

    @app.route('/api/building-maps/<map_id>/zones/batch', methods=['POST'])
    @require_admin
    def api_batch_create_building_zones(user_id, role, map_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get(sb, map_id)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        if str(bm.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        zones_data = data.get('zones', [])
        if not isinstance(zones_data, list) or not zones_data:
            return jsonify({'error': 'zones must be a non-empty list'}), 400
        for z in zones_data:
            if not isinstance(z.get('points', []), list) or len(z.get('points', [])) < 3:
                return jsonify({'error': 'Each zone needs at least 3 points'}), 400
        created = bm_batch_create_zones(sb, map_id, zones_data)
        return jsonify(created), 201

    @app.route('/api/building-zones/<int:zone_id>', methods=['PUT'])
    @require_admin
    def api_update_building_zone(user_id, role, zone_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        zone = bm_get_zone(sb, zone_id)
        if not zone:
            return jsonify({'error': 'Not found'}), 404
        bm = bm_get(sb, zone['building_map_id'])
        if not bm or (str(bm.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        updates = {}
        for k in ('name', 'floor_number', 'color', 'sort_order', 'points',
                   'linked_panorama_id', 'linked_floor_plan_item_id', 'image_id'):
            if k in data:
                updates[k] = data[k]
        if updates:
            bm_update_zone(sb, zone_id, **updates)
        updated = bm_get_zone(sb, zone_id)
        return jsonify(updated)

    @app.route('/api/building-zones/<int:zone_id>', methods=['DELETE'])
    @require_admin
    def api_delete_building_zone(user_id, role, zone_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        zone = bm_get_zone(sb, zone_id)
        if not zone:
            return jsonify({'error': 'Not found'}), 404
        bm = bm_get(sb, zone['building_map_id'])
        if not bm or (str(bm.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        bm_delete_zone(sb, zone_id)
        return jsonify({'success': True})

    # -- Building Map pages --

    @app.route('/building-map/admin/<map_id>')
    def building_map_admin_page(map_id):
        return render_template('building_map_admin.html', map_id=map_id, **auth_ctx())

    @app.route('/building-map/view/<share_token>')
    def building_map_public_view(share_token):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        bm = bm_get_by_token(sb, share_token)
        if not bm:
            return "Not found", 404
        # Collect images for this building map
        images = bm_list_images(sb, bm['id'])
        for img in images:
            img['image_url'] = get_building_map_s3_url(img.get('image_filename'))
        # Fallback: if no images table entries, use the building map's own image
        if not images:
            images = [{
                'id': None,
                'name': bm.get('name', 'View'),
                'image_url': get_building_map_s3_url(bm.get('image_filename')),
                'image_width': bm.get('image_width', 0),
                'image_height': bm.get('image_height', 0),
            }]
        zones = bm_list_zones(sb, bm['id'])
        # Enrich zones with linked floor plan image URLs
        for z in zones:
            if z.get('linked_floor_plan_item_id'):
                fp_item = fp_get_item(sb, z['linked_floor_plan_item_id'])
                if fp_item:
                    z['linked_floor_plan_image_url'] = get_floorplan_s3_url(fp_item.get('image_filename'))
                    z['linked_floor_plan_name'] = fp_item.get('name', '')
        # Get all building maps for this user to allow switching
        all_maps = bm_list(sb, bm['user_id'])
        siblings = []
        for m in all_maps:
            siblings.append({
                'id': m['id'],
                'name': m['name'],
                'share_token': m.get('share_token', ''),
                'active': m['id'] == bm['id'],
            })
        return render_template('building_map_view.html',
                               bm=bm,
                               images=images,
                               image_url=images[0]['image_url'] if images else '',
                               zones=zones,
                               siblings=siblings)

    # -- Public API for building map data (used by customer view JS) --

    @app.route('/api/public/building-map/<share_token>')
    def api_public_building_map(share_token):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        bm = bm_get_by_token(sb, share_token)
        if not bm:
            return jsonify({'error': 'Not found'}), 404
        bm['image_url'] = get_building_map_s3_url(bm.get('image_filename'))
        zones = bm_list_zones(sb, bm['id'])
        for z in zones:
            if z.get('linked_floor_plan_item_id'):
                fp_item = fp_get_item(sb, z['linked_floor_plan_item_id'])
                if fp_item:
                    z['linked_floor_plan_image_url'] = get_floorplan_s3_url(fp_item.get('image_filename'))
                    z['linked_floor_plan_name'] = fp_item.get('name', '')
        bm['zones'] = zones
        return jsonify(bm)

    # ===================================================================
    # PROJECT PLANS
    # ===================================================================

    @app.route('/api/project-plans', methods=['GET'])
    @require_auth
    def api_list_project_plans(user_id, role):
        sb = get_supabase()
        plans = pp_list(sb, user_id)
        base = (request.url_root or '').rstrip('/')
        for p in plans:
            maps = pp_list_maps(sb, p['id'])
            p['map_count'] = len(maps)
            map_names = []
            total_zones = 0
            total_floors = 0
            for link in maps:
                bm = bm_get(sb, link.get('building_map_id'))
                if bm:
                    map_names.append(bm.get('name', ''))
                    zones = bm_list_zones(sb, bm['id'])
                    total_zones += len(zones)
                    total_floors += len(set(z.get('floor_number', 0) for z in zones))
            p['map_names'] = map_names
            p['total_zones'] = total_zones
            p['total_floors'] = total_floors
            p['share_url'] = f"{base}/project-plan/view/{p.get('share_token', '')}"
        return jsonify(plans)

    @app.route('/api/project-plans', methods=['POST'])
    @require_auth
    def api_create_project_plan(user_id, role):
        sb = get_supabase()
        data = request.get_json(force=True)
        name = data.get('name', 'Project Plan').strip() or 'Project Plan'
        existing = pp_list(sb, user_id)
        if any(p.get('name', '').strip().lower() == name.lower() for p in existing):
            return jsonify({'error': f'A project plan named "{name}" already exists'}), 409
        org_id = data.get('org_id')
        plan = pp_create(sb, user_id, org_id, name)
        if not plan:
            return jsonify({'error': 'Failed to create project plan'}), 500
        plan['share_url'] = request.host_url.rstrip('/') + '/project-plan/view/' + plan.get('share_token', '')
        return jsonify(plan), 201

    @app.route('/api/project-plans/<plan_id>', methods=['GET'])
    @require_auth
    def api_get_project_plan(user_id, role, plan_id):
        sb = get_supabase()
        plan = pp_get(sb, plan_id)
        if not plan:
            return jsonify({'error': 'Not found'}), 404
        plan['maps'] = pp_list_maps(sb, plan_id)
        # Enrich maps with building map details
        for link in plan['maps']:
            bm = bm_get(sb, link['building_map_id'])
            if bm:
                link['building_map_name'] = bm.get('name', '')
                link['building_map_image_url'] = get_building_map_s3_url(bm.get('image_filename'))
                link['building_map_share_token'] = bm.get('share_token', '')
        plan['share_url'] = request.host_url.rstrip('/') + '/project-plan/view/' + plan.get('share_token', '')
        return jsonify(plan)

    @app.route('/api/project-plans/<plan_id>', methods=['PATCH'])
    @require_auth
    def api_update_project_plan(user_id, role, plan_id):
        sb = get_supabase()
        data = request.get_json(force=True)
        updated = pp_update(sb, plan_id, **data)
        if not updated:
            return jsonify({'error': 'Not found or no valid fields'}), 404
        return jsonify(updated)

    @app.route('/api/project-plans/<plan_id>', methods=['DELETE'])
    @require_auth
    def api_delete_project_plan(user_id, role, plan_id):
        sb = get_supabase()
        deleted = pp_delete(sb, plan_id)
        if not deleted:
            return jsonify({'error': 'Not found'}), 404
        return jsonify({'ok': True})

    # -- Plan ↔ Map linking --

    @app.route('/api/project-plans/<plan_id>/maps', methods=['GET'])
    @require_auth
    def api_list_plan_maps(user_id, role, plan_id):
        sb = get_supabase()
        maps = pp_list_maps(sb, plan_id)
        for link in maps:
            bm = bm_get(sb, link['building_map_id'])
            if bm:
                link['building_map_name'] = bm.get('name', '')
                link['building_map_image_url'] = get_building_map_s3_url(bm.get('image_filename'))
                link['building_map_share_token'] = bm.get('share_token', '')
        return jsonify(maps)

    @app.route('/api/project-plans/<plan_id>/maps', methods=['POST'])
    @require_auth
    def api_add_map_to_plan(user_id, role, plan_id):
        sb = get_supabase()
        data = request.get_json(force=True)
        building_map_id = data.get('building_map_id')
        if not building_map_id:
            return jsonify({'error': 'building_map_id required'}), 400
        sort_order = pp_next_sort(sb, plan_id)
        link = pp_add_map(sb, plan_id, building_map_id, sort_order)
        if not link:
            return jsonify({'error': 'Failed to add map'}), 500
        return jsonify(link), 201

    @app.route('/api/project-plans/<plan_id>/maps/<building_map_id>', methods=['DELETE'])
    @require_auth
    def api_remove_map_from_plan(user_id, role, plan_id, building_map_id):
        sb = get_supabase()
        pp_remove_map(sb, plan_id, building_map_id)
        return jsonify({'ok': True})

    @app.route('/api/project-plans/<plan_id>/maps/reorder', methods=['POST'])
    @require_auth
    def api_reorder_plan_maps(user_id, role, plan_id):
        sb = get_supabase()
        data = request.get_json(force=True)
        ordered_ids = data.get('ordered_map_ids', [])
        result = pp_reorder(sb, plan_id, ordered_ids)
        return jsonify(result)

    # -- Public Project Plan view --

    @app.route('/project-plan/view/<share_token>')
    def project_plan_public_view(share_token):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        plan = pp_get_by_token(sb, share_token)
        if not plan:
            return "Not found", 404
        links = pp_list_maps(sb, plan['id'])
        buildings = []
        for link in links:
            bm = bm_get(sb, link['building_map_id'])
            if not bm:
                continue
            bm['image_url'] = get_building_map_s3_url(bm.get('image_filename'))
            bm_zones = bm_list_zones(sb, bm['id'])
            for z in bm_zones:
                if z.get('linked_floor_plan_item_id'):
                    fp_item = fp_get_item(sb, z['linked_floor_plan_item_id'])
                    if fp_item:
                        z['linked_floor_plan_image_url'] = get_floorplan_s3_url(fp_item.get('image_filename'))
                        z['linked_floor_plan_name'] = fp_item.get('name', '')
            bm['zones'] = bm_zones
            buildings.append(bm)
        return render_template('project_plan_view.html',
                               plan=plan,
                               buildings=buildings)

    # ==================================================================
    # Galleries
    # ==================================================================

    @app.route('/api/galleries', methods=['GET'])
    @require_admin
    def api_list_galleries(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        galleries = gal_list(sb, user_id)
        base = (request.url_root or '').rstrip('/')
        for g in galleries:
            g['share_url'] = f"{base}/gallery/view/{g.get('share_token', '')}"
            items = gal_list_items(sb, g['id'])
            g['item_count'] = len(items)
            g['item_names'] = [it.get('name', '') for it in items[:5]]
            g['image_count'] = sum(1 for it in items if it.get('media_type') == 'image')
            g['video_count'] = sum(1 for it in items if it.get('media_type') == 'video')
        return jsonify(galleries)

    @app.route('/api/galleries', methods=['POST'])
    @require_admin
    def api_create_gallery(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or 'Gallery').strip()
        existing = gal_list(sb, user_id)
        if any(g.get('name', '').strip().lower() == name.lower() for g in existing):
            return jsonify({'error': f'A gallery named "{name}" already exists'}), 409
        profile = get_profile(sb, user_id)
        org_id = profile.get('org_id') if profile else None
        gal = gal_create(sb, user_id, org_id, name)
        if not gal:
            return jsonify({'error': 'Failed to create gallery'}), 500
        base = (request.url_root or '').rstrip('/')
        gal['share_url'] = f"{base}/gallery/view/{gal.get('share_token', '')}"
        return jsonify(gal), 201

    @app.route('/api/galleries/<gallery_id>', methods=['GET'])
    @require_admin
    def api_get_gallery(user_id, role, gallery_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        gal = gal_get(sb, gallery_id)
        if not gal:
            return jsonify({'error': 'Not found'}), 404
        if str(gal.get('user_id')) != str(user_id) and role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        items = gal_list_items(sb, gallery_id)
        for item in items:
            item['media_url'] = get_gallery_s3_url(item.get('filename'))
        gal['items'] = items
        base = (request.url_root or '').rstrip('/')
        gal['share_url'] = f"{base}/gallery/view/{gal.get('share_token', '')}"
        return jsonify(gal)

    @app.route('/api/galleries/<gallery_id>', methods=['PATCH'])
    @require_admin
    def api_update_gallery(user_id, role, gallery_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        gal = gal_get(sb, gallery_id)
        if not gal or (str(gal.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        name = data.get('name')
        if name:
            gal_update(sb, gallery_id, name=name)
        updated = gal_get(sb, gallery_id)
        return jsonify(updated)

    @app.route('/api/galleries/<gallery_id>', methods=['DELETE'])
    @require_admin
    def api_delete_gallery(user_id, role, gallery_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        gal = gal_get(sb, gallery_id)
        if not gal or (str(gal.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        items = gal_list_items(sb, gallery_id)
        for item in items:
            delete_gallery_from_s3(item.get('filename'))
        gal_delete(sb, gallery_id)
        return jsonify({'success': True})

    # -- Gallery Items --

    @app.route('/api/galleries/<gallery_id>/items', methods=['GET'])
    @require_admin
    def api_list_gallery_items(user_id, role, gallery_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        gal = gal_get(sb, gallery_id)
        if not gal or (str(gal.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        items = gal_list_items(sb, gallery_id)
        for item in items:
            item['media_url'] = get_gallery_s3_url(item.get('filename'))
        return jsonify(items)

    @app.route('/api/galleries/<gallery_id>/items', methods=['POST'])
    @require_admin
    def api_upload_gallery_item(user_id, role, gallery_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        gal = gal_get(sb, gallery_id)
        if not gal or (str(gal.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        name = request.form.get('name', '').strip() or 'Media'
        f = request.files.get('file')
        if not f or not f.filename:
            return jsonify({'error': 'No file provided'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        is_video = ext in ALLOWED_GALLERY_VIDEO_EXT
        is_image = ext in ALLOWED_GALLERY_IMAGE_EXT
        if not is_video and not is_image:
            return jsonify({'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_GALLERY_IMAGE_EXT | ALLOWED_GALLERY_VIDEO_EXT)}'}), 400
        raw_bytes = f.read()
        if not raw_bytes or len(raw_bytes) > MAX_GALLERY_READ_BYTES:
            return jsonify({'error': 'File too large (max 100MB)'}), 400
        if is_image:
            processed, w, h = compress_gallery_image(raw_bytes)
            if not processed:
                return jsonify({'error': 'Failed to process image'}), 400
            filename = f"gal_{uuid.uuid4().hex[:16]}.jpg"
            upload_gallery_to_s3(filename, processed, 'image/jpeg')
            file_size = len(processed)
        else:
            content_types = {'mp4': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime'}
            filename = f"gal_{uuid.uuid4().hex[:16]}.{ext}"
            upload_gallery_to_s3(filename, raw_bytes, content_types.get(ext, 'video/mp4'))
            file_size = len(raw_bytes)
            w, h = 0, 0
        sort_order = gal_next_sort_order(sb, gallery_id)
        item = gal_create_item(
            sb, gallery_id, name, filename,
            media_type='video' if is_video else 'image',
            media_width=w, media_height=h,
            file_size_bytes=file_size,
            sort_order=sort_order,
        )
        if not item:
            return jsonify({'error': 'Failed to create item'}), 500
        item['media_url'] = get_gallery_s3_url(filename)
        return jsonify(item), 201

    @app.route('/api/gallery-items/<item_id>', methods=['PATCH'])
    @require_admin
    def api_update_gallery_item(user_id, role, item_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        item = gal_get_item(sb, item_id)
        if not item:
            return jsonify({'error': 'Not found'}), 404
        gal = gal_get(sb, item['gallery_id'])
        if not gal or (str(gal.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        updates = {}
        if 'name' in data:
            updates['name'] = str(data['name']).strip()
        if updates:
            gal_update_item(sb, item_id, **updates)
        updated = gal_get_item(sb, item_id)
        if updated:
            updated['media_url'] = get_gallery_s3_url(updated.get('filename'))
        return jsonify(updated)

    @app.route('/api/gallery-items/<item_id>', methods=['DELETE'])
    @require_admin
    def api_delete_gallery_item(user_id, role, item_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        item = gal_get_item(sb, item_id)
        if not item:
            return jsonify({'error': 'Not found'}), 404
        gal = gal_get(sb, item['gallery_id'])
        if not gal or (str(gal.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        delete_gallery_from_s3(item.get('filename'))
        gal_delete_item(sb, item_id)
        return jsonify({'success': True})

    @app.route('/api/galleries/<gallery_id>/reorder', methods=['POST'])
    @require_admin
    def api_reorder_gallery_items(user_id, role, gallery_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        gal = gal_get(sb, gallery_id)
        if not gal or (str(gal.get('user_id')) != str(user_id) and role != 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        data = request.get_json(silent=True) or {}
        ordered_ids = data.get('ordered_ids')
        if not ordered_ids or not isinstance(ordered_ids, list):
            return jsonify({'error': 'ordered_ids must be a non-empty list'}), 400
        gal_reorder_items(sb, gallery_id, ordered_ids)
        items = gal_list_items(sb, gallery_id)
        for item in items:
            item['media_url'] = get_gallery_s3_url(item.get('filename'))
        return jsonify(items)

    # -- Public Gallery view --

    @app.route('/gallery/view/<share_token>')
    def gallery_public_view(share_token):
        sb = get_supabase()
        if not sb:
            return "Database not configured", 503
        gal = gal_get_by_token(sb, share_token)
        if not gal:
            return "Not found", 404
        items = gal_list_items(sb, gal['id'])
        for item in items:
            item['media_url'] = get_gallery_s3_url(item.get('filename'))
        items = [it for it in items if it.get('media_url')]
        if not items:
            return "No media uploaded yet", 404
        return render_template('gallery_view.html',
                               gallery=gal,
                               items=items)

    # ── User Management (admin-only) ──────────────────────────────────

    def _get_caller_org_id(sb, user_id):
        """Return the org_id for the calling user, or None."""
        profile = get_profile(sb, user_id) or {}
        return profile.get('org_id')

    def _is_target_admin(sb, target_user_id):
        """Check if a target user is admin or superadmin."""
        target = get_profile(sb, target_user_id) or {}
        return target.get('role') in ('admin', 'superadmin')

    def _can_manage_target(sb, caller_id, caller_role, target_user_id):
        """
        Returns (ok, error_msg, status_code).
        Admin cannot modify another admin. Superadmin can manage anyone.
        Both must share the same org (unless superadmin).
        """
        caller_profile = get_profile(sb, caller_id) or {}
        caller_org = caller_profile.get('org_id')
        if not caller_org:
            return False, 'Your organization is not set', 403

        target_profile = get_profile(sb, target_user_id) or {}
        target_org = target_profile.get('org_id')
        target_role = target_profile.get('role', 'user')

        if caller_role != 'superadmin':
            if not target_org or str(target_org) != str(caller_org):
                return False, 'User is not in your organization', 403
            if target_role in ('admin', 'superadmin'):
                return False, 'You cannot modify another admin user', 403
            if str(target_user_id) == str(caller_id):
                return False, 'You cannot modify your own account here', 403
        return True, None, None

    @app.route('/api/admin/org-users', methods=['GET'])
    @require_admin
    def admin_list_org_users(user_id, role):
        """List all users in the admin's org, with role info."""
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        profile = get_profile(sb, user_id) or {}
        org_id = profile.get('org_id')
        if not org_id:
            return jsonify({'error': 'Your organization is not set'}), 403
        try:
            r = sb.table('profiles').select(
                'user_id, display_name, email, role, created_at'
            ).eq('org_id', org_id).order('created_at', desc=True).execute()
            users = []
            for row in (r.data or []):
                if not row.get('user_id') or not str(row['user_id']).strip():
                    continue
                o = dict(row)
                o['user_id'] = str(o['user_id'])
                o['is_self'] = str(o['user_id']) == str(user_id)
                # Admin can't manage other admins (but superadmin can)
                o['can_manage'] = (
                    role == 'superadmin'
                    or (o['role'] not in ('admin', 'superadmin') and not o['is_self'])
                )
                if o.get('created_at'):
                    o['created_at'] = str(o['created_at'])
                users.append(o)
            return jsonify(users)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/admin/invite-user', methods=['POST'])
    @require_admin
    def admin_invite_user(user_id, role):
        """Send an email invite to a new user. display_name is required."""
        if not app_config.SUPABASE_URL or not app_config.SUPABASE_SERVICE_ROLE_KEY:
            return jsonify({'error': 'Server not configured for inviting users'}), 503
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        caller_profile = get_profile(sb, user_id) or {}
        org_id = caller_profile.get('org_id')
        if not org_id:
            return jsonify({'error': 'Your organization is not set'}), 403

        data = request.get_json() or {}
        email = (data.get('email') or '').strip().lower()
        display_name = (data.get('display_name') or '').strip()
        invite_role = str(data.get('role') or 'user').strip().lower()
        if invite_role not in ('admin', 'user'):
            invite_role = 'user'
        if not email or '@' not in email:
            return jsonify({'error': 'Valid email is required'}), 400
        if not display_name:
            return jsonify({'error': 'Display name is required when inviting a user'}), 400

        # Check if user already exists in this org
        try:
            existing = sb.table('profiles').select('user_id, email').eq('email', email).limit(1).execute()
            if existing.data and len(existing.data) > 0:
                return jsonify({'error': 'A user with this email already exists'}), 409
        except Exception:
            pass

        try:
            from supabase import create_client
            admin_sb = create_client(app_config.SUPABASE_URL, app_config.SUPABASE_SERVICE_ROLE_KEY)

            # Use invite_user_by_email to send the invite email
            resp = admin_sb.auth.admin.invite_user_by_email(
                email,
                options={
                    'data': {
                        'display_name': display_name,
                    }
                }
            )
            new_uid = resp.user.id

            # Create the profile immediately so display_name is stored
            profile_row = {
                'user_id': str(new_uid),
                'role': invite_role,
                'email': email,
                'display_name': display_name,
                'org_id': str(org_id),
            }
            try:
                admin_sb.table('profiles').upsert(profile_row, on_conflict='user_id').execute()
            except Exception:
                pass

            # Record the invite
            try:
                admin_sb.table('user_invites').insert({
                    'org_id': str(org_id),
                    'invited_by': str(user_id),
                    'email': email,
                    'display_name': display_name,
                    'role': invite_role,
                    'status': 'pending',
                    'invited_user_id': str(new_uid),
                }).execute()
            except Exception:
                pass  # invite tracking is best-effort

            return jsonify({
                'success': True,
                'user': {
                    'id': str(new_uid),
                    'email': email,
                    'display_name': display_name,
                    'role': invite_role,
                }
            }), 201
        except Exception as e:
            err = str(e)
            if 'already registered' in err.lower() or 'already exists' in err.lower():
                return jsonify({'error': 'A user with this email already exists'}), 409
            return jsonify({'error': err or 'Failed to invite user'}), 400

    @app.route('/api/admin/users/<target_user_id>/details', methods=['PUT', 'PATCH'])
    @require_admin
    def admin_update_user_details(user_id, role, target_user_id):
        """Update display_name and/or email for a user in the org."""
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503

        ok, err_msg, status = _can_manage_target(sb, user_id, role, target_user_id)
        if not ok:
            return jsonify({'error': err_msg}), status

        data = request.get_json() or {}
        payload = {'updated_at': datetime.utcnow().isoformat()}
        if 'display_name' in data:
            dn = (data.get('display_name') or '').strip()
            if not dn:
                return jsonify({'error': 'Display name cannot be empty'}), 400
            payload['display_name'] = dn
        if 'email' in data:
            em = (data.get('email') or '').strip().lower()
            if not em or '@' not in em:
                return jsonify({'error': 'Valid email is required'}), 400
            payload['email'] = em

        if len(payload) <= 1:
            return jsonify({'error': 'Provide display_name and/or email'}), 400

        try:
            sb.table('profiles').update(payload).eq('user_id', target_user_id).execute()

            # Also update email in Supabase Auth if changed
            if 'email' in data and app_config.SUPABASE_SERVICE_ROLE_KEY:
                try:
                    from supabase import create_client
                    admin_sb = create_client(app_config.SUPABASE_URL, app_config.SUPABASE_SERVICE_ROLE_KEY)
                    admin_sb.auth.admin.update_user_by_id(
                        str(target_user_id),
                        {'email': payload['email']}
                    )
                except Exception:
                    pass

            # Update display_name in Supabase Auth user_metadata
            if 'display_name' in data and app_config.SUPABASE_SERVICE_ROLE_KEY:
                try:
                    from supabase import create_client
                    admin_sb = create_client(app_config.SUPABASE_URL, app_config.SUPABASE_SERVICE_ROLE_KEY)
                    admin_sb.auth.admin.update_user_by_id(
                        str(target_user_id),
                        {'user_metadata': {'display_name': payload['display_name']}}
                    )
                except Exception:
                    pass

            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/admin/users/<target_user_id>/password', methods=['PUT'])
    @require_admin
    def admin_reset_user_password(user_id, role, target_user_id):
        """Reset password for a user in the org. Admin cannot reset another admin's password."""
        if not app_config.SUPABASE_URL or not app_config.SUPABASE_SERVICE_ROLE_KEY:
            return jsonify({'error': 'Server not configured'}), 503
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503

        ok, err_msg, status = _can_manage_target(sb, user_id, role, target_user_id)
        if not ok:
            return jsonify({'error': err_msg}), status

        data = request.get_json() or {}
        new_password = data.get('password') or ''
        if len(new_password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400

        try:
            from supabase import create_client
            admin_sb = create_client(app_config.SUPABASE_URL, app_config.SUPABASE_SERVICE_ROLE_KEY)
            admin_sb.auth.admin.update_user_by_id(
                str(target_user_id),
                {'password': new_password}
            )
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e) or 'Failed to update password'}), 500

    @app.route('/api/admin/users/<target_user_id>/role', methods=['PUT'])
    @require_admin
    def admin_toggle_user_role(user_id, role, target_user_id):
        """Toggle a user's role between 'user' and 'admin'. Admin cannot change another admin."""
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503

        data = request.get_json() or {}
        new_role = (data.get('role') or 'user').lower()
        if new_role not in ('admin', 'user'):
            return jsonify({'error': 'Role must be admin or user'}), 400

        # Check target's current role
        target_profile = get_profile(sb, target_user_id) or {}
        target_current_role = target_profile.get('role', 'user')
        target_org = target_profile.get('org_id')

        caller_profile = get_profile(sb, user_id) or {}
        caller_org = caller_profile.get('org_id')

        if not caller_org:
            return jsonify({'error': 'Your organization is not set'}), 403
        if str(target_user_id) == str(user_id):
            return jsonify({'error': 'You cannot change your own role'}), 403

        if role != 'superadmin':
            if not target_org or str(target_org) != str(caller_org):
                return jsonify({'error': 'User is not in your organization'}), 403
            # Admin can only promote/demote non-admin users
            if target_current_role in ('admin', 'superadmin'):
                return jsonify({'error': 'You cannot modify another admin user'}), 403

        try:
            sb.table('profiles').update({
                'role': new_role,
                'updated_at': datetime.utcnow().isoformat()
            }).eq('user_id', target_user_id).execute()
            return jsonify({'success': True, 'new_role': new_role})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/admin/users/<target_user_id>', methods=['DELETE'])
    @require_admin
    def admin_delete_user(user_id, role, target_user_id):
        """Delete a user from the org. Admin can only delete normal users."""
        if not app_config.SUPABASE_URL or not app_config.SUPABASE_SERVICE_ROLE_KEY:
            return jsonify({'error': 'Server not configured'}), 503
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503

        ok, err_msg, status = _can_manage_target(sb, user_id, role, target_user_id)
        if not ok:
            return jsonify({'error': err_msg}), status

        try:
            from supabase import create_client
            admin_sb = create_client(app_config.SUPABASE_URL, app_config.SUPABASE_SERVICE_ROLE_KEY)
            # Delete the profile first
            admin_sb.table('profiles').delete().eq('user_id', target_user_id).execute()
            # Delete the auth user (cascades related data via FK)
            admin_sb.auth.admin.delete_user(str(target_user_id))
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e) or 'Failed to delete user'}), 500

    @app.route('/api/admin/invites', methods=['GET'])
    @require_admin
    def admin_list_invites(user_id, role):
        """List all invites sent for the admin's org."""
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        org_id = _get_caller_org_id(sb, user_id)
        if not org_id:
            return jsonify({'error': 'Your organization is not set'}), 403
        try:
            r = sb.table('user_invites').select(
                'id, email, display_name, role, status, created_at'
            ).eq('org_id', org_id).order('created_at', desc=True).execute()
            out = []
            for row in (r.data or []):
                o = dict(row)
                if o.get('created_at'):
                    o['created_at'] = str(o['created_at'])
                out.append(o)
            return jsonify(out)
        except Exception as e:
            msg = str(e)
            if 'user_invites' in msg and ('does not exist' in msg.lower() or 'relation' in msg.lower()):
                return jsonify([])  # table not yet created, return empty
            return jsonify({'error': msg}), 500
