"""
Supabase DB helpers: JWT verification, profiles, panoramas, plots, panorama_access.
"""
import os
import json
from functools import wraps
from flask import request, jsonify

try:
    import jwt
except ImportError:
    jwt = None

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET', '')

_sb_client = None
_httpx_client = None


def _get_httpx_client():
    """Lazy httpx client for Auth API (SSL verify from env)."""
    global _httpx_client
    if _httpx_client is None:
        verify = os.environ.get('SUPABASE_SSL_VERIFY', 'true').lower() not in ('false', '0', 'no')
        import httpx
        _httpx_client = httpx.Client(verify=verify, timeout=10)
    return _httpx_client


def get_supabase():
    """Lazy Supabase client (service role)."""
    global _sb_client
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return None
    if _sb_client is None:
        try:
            from supabase import create_client
            options = None
            if os.environ.get('SUPABASE_SSL_VERIFY', 'true').lower() in ('false', '0', 'no'):
                import httpx
                from supabase.lib.client_options import SyncClientOptions
                options = SyncClientOptions(httpx_client=httpx.Client(verify=False))
            _sb_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, options=options)
        except Exception:
            return None
    return _sb_client


def get_current_user():
    """
    From Authorization: Bearer <jwt>, verify and return (user_id, role) or (None, None).
    Tries HS256 decode first; if token uses another alg (e.g. ES256), validates via Supabase Auth /user.
    """
    auth = request.headers.get('Authorization') or request.headers.get('authorization')
    if not auth or not auth.startswith('Bearer '):
        return None, None
    token = auth[7:].strip()
    if not token:
        return None, None
    user_id = None
    if SUPABASE_JWT_SECRET and jwt:
        try:
            payload = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=['HS256'])
            user_id = payload.get('sub')
        except Exception:
            pass
    if not user_id and SUPABASE_URL and SUPABASE_ANON_KEY:
        try:
            client = _get_httpx_client()
            r = client.get(
                f'{SUPABASE_URL}/auth/v1/user',
                headers={'apikey': SUPABASE_ANON_KEY, 'Authorization': f'Bearer {token}'},
            )
            if r.status_code == 200:
                data = r.json()
                user_id = (data.get('id') or data.get('user', {}).get('id'))
        except Exception:
            pass
    if not user_id:
        return None, None
    sb = get_supabase()
    if not sb:
        return user_id, 'user'
    profile = get_profile(sb, user_id)
    role = (profile or {}).get('role', 'user')
    return user_id, role


def get_profile(sb, user_id):
    """Fetch profile row for user_id."""
    try:
        r = sb.table('profiles').select('*').eq('user_id', user_id).limit(1).execute()
        if r.data and len(r.data) > 0:
            return r.data[0]
    except Exception:
        pass
    return None


def _sync_profile_display_name_from_auth(sb, user_id):
    """Backfill profile display_name and email from Supabase Auth user_metadata when profile has no display_name."""
    try:
        resp = sb.auth.admin.get_user_by_id(str(user_id))
        user = getattr(resp, 'user', resp)
        if not user:
            return
        meta = getattr(user, 'user_metadata', None) or {}
        display_name = (meta.get('display_name') or '').strip() or None
        email = (getattr(user, 'email', None) or '').strip() or None
        if not display_name and not email:
            return
        from datetime import datetime
        upd = {'updated_at': datetime.utcnow().isoformat()}
        if display_name is not None:
            upd['display_name'] = display_name
        if email is not None:
            upd['email'] = email
        sb.table('profiles').update(upd).eq('user_id', user_id).execute()
    except Exception:
        pass


def ensure_profile(sb, user_id, role='user'):
    """Insert or update profile with role."""
    existing = get_profile(sb, user_id)
    if existing:
        if not (existing.get('display_name') or '').strip():
            _sync_profile_display_name_from_auth(sb, user_id)
            existing = get_profile(sb, user_id) or existing
        return existing
    try:
        sb.table('profiles').upsert(
            {'user_id': user_id, 'role': role},
            on_conflict='user_id'
        ).execute()
    except Exception:
        try:
            sb.table('profiles').insert({'user_id': user_id, 'role': role}).execute()
        except Exception:
            pass
    return get_profile(sb, user_id) or {'user_id': user_id, 'role': role}


def require_auth(f):
    """Decorator: require valid JWT; inject (user_id, role) as first args. Return 401 if no token."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        user_id, role = get_current_user()
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        sb = get_supabase()
        if sb:
            ensure_profile(sb, user_id, role or 'user')
        return f(user_id, role, *args, **kwargs)
    return wrapped


def require_admin(f):
    """Decorator: require auth and role admin."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        user_id, role = get_current_user()
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        if role not in ('admin', 'superadmin'):
            return jsonify({'error': 'Forbidden'}), 403
        return f(user_id, role, *args, **kwargs)
    return wrapped


def require_superadmin(f):
    """Decorator: require auth and role superadmin."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        user_id, role = get_current_user()
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        if role != 'superadmin':
            return jsonify({'error': 'Forbidden'}), 403
        return f(user_id, role, *args, **kwargs)
    return wrapped


def get_panorama_with_access(sb, panorama_id, user_id):
    """
    Return (panorama_dict, access_type) or (None, None).
    access_type: 'owner' | 'client' | 'viewer'
    """
    try:
        r = sb.table('panoramas').select('*').eq('id', panorama_id).limit(1).execute()
        if not r.data or len(r.data) == 0:
            return None, None
        p = r.data[0]
        if p.get('user_id') == user_id:
            return p, 'owner'
        acc = sb.table('panorama_access').select('access_type').eq('panorama_id', panorama_id).eq('user_id', user_id).limit(1).execute()
        if acc.data and len(acc.data) > 0:
            return p, acc.data[0].get('access_type', 'viewer')

        # Folder share: if this panorama belongs to a workspace, check workspace_access.
        ws_id = None
        try:
            ws_id = p.get('workspace_id')
        except Exception:
            ws_id = None
        if ws_id:
            try:
                wacc = (
                    sb.table('workspace_access')
                    .select('access_type')
                    .eq('workspace_id', ws_id)
                    .eq('user_id', user_id)
                    .limit(1)
                    .execute()
                )
                if wacc.data and len(wacc.data) > 0:
                    return p, wacc.data[0].get('access_type', 'viewer')
            except Exception:
                # Backward-compatible when workspace_access table doesn't exist yet.
                pass
        return None, None
    except Exception:
        return None, None


# Columns for panorama list/detail (exclude image_data blob)
_PANORAMA_FIELDS_BASE = 'id, user_id, name, filename, original_filename, width, height, is_360, created_at, updated_at'
_PANORAMA_FIELDS_WITH_WORKSPACE = _PANORAMA_FIELDS_BASE + ', workspace_id'


def _access_rank(access_type):
    at = str(access_type or 'viewer').lower()
    if at == 'owner':
        return 3
    if at == 'client':
        return 2
    return 1


def _normalize_access_type(access_type):
    at = str(access_type or 'viewer').lower().strip()
    return at if at in ('owner', 'client', 'viewer') else 'viewer'


def _select_panorama_rows(sb, selector, values):
    if not values:
        return []

    def _run(fields):
        return (
            sb.table('panoramas')
            .select(fields)
            .in_(selector, values)
            .execute()
        )

    try:
        r = _run(_PANORAMA_FIELDS_WITH_WORKSPACE)
    except Exception as e:
        if 'workspace_id' in str(e).lower():
            r = _run(_PANORAMA_FIELDS_BASE)
        else:
            raise
    return r.data or []


def _plot_counts_for_panoramas(sb, panorama_ids):
    ids = []
    seen = set()
    for pid in (panorama_ids or []):
        if pid is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)

    if not ids:
        return {}

    counts = {pid: 0 for pid in ids}
    chunk_size = 150
    try:
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            r = (
                sb.table('plots')
                .select('panorama_id')
                .in_('panorama_id', chunk)
                .limit(50000)
                .execute()
            )
            for row in (r.data or []):
                pid = row.get('panorama_id')
                key = pid
                if key not in counts:
                    try:
                        key = int(pid)
                    except Exception:
                        key = pid
                if key in counts:
                    counts[key] = int(counts.get(key, 0)) + 1
    except Exception:
        # Fallback to per-panorama counting if bulk query fails for any reason.
        for pid in ids:
            counts[pid] = _count_plots(sb, pid)
    return counts


def list_panoramas_for_user(sb, user_id):
    """
    Return list of panoramas the user can see, each with access_type ('owner'|'client'|'viewer') and plot_count.
    """
    try:
        def _normalize_panorama_id(value):
            try:
                return int(value)
            except Exception:
                return value

        # Owned panoramas
        try:
            owned = (
                sb.table('panoramas')
                .select(_PANORAMA_FIELDS_WITH_WORKSPACE)
                .eq('user_id', user_id)
                .order('updated_at', desc=True)
                .execute()
            )
        except Exception as e:
            if 'workspace_id' in str(e).lower():
                owned = (
                    sb.table('panoramas')
                    .select(_PANORAMA_FIELDS_BASE)
                    .eq('user_id', user_id)
                    .order('updated_at', desc=True)
                    .execute()
                )
            else:
                raise

        # Direct panorama shares.
        shared = (
            sb.table('panorama_access')
            .select('panorama_id, access_type')
            .eq('user_id', user_id)
            .execute()
        )
        shared_access = {}
        for row in (shared.data or []):
            pid = _normalize_panorama_id(row.get('panorama_id'))
            if pid is None:
                continue
            at = _normalize_access_type(row.get('access_type'))
            current = shared_access.get(pid)
            if current is None or _access_rank(at) > _access_rank(current):
                shared_access[pid] = at

        # Folder shares.
        try:
            workspace_access = (
                sb.table('workspace_access')
                .select('workspace_id, access_type')
                .eq('user_id', user_id)
                .execute()
            )
            workspace_access_rows = workspace_access.data or []
        except Exception:
            workspace_access_rows = []

        workspace_to_access = {}
        for row in workspace_access_rows:
            wsid = row.get('workspace_id')
            if not wsid:
                continue
            key = str(wsid)
            at = _normalize_access_type(row.get('access_type'))
            current = workspace_to_access.get(key)
            if current is None or _access_rank(at) > _access_rank(current):
                workspace_to_access[key] = at

        shared_ids = list(shared_access.keys())
        workspace_ids = list(workspace_to_access.keys())

        shared_panos = []
        if shared_ids:
            try:
                shared_panos = _select_panorama_rows(sb, 'id', shared_ids)
            except Exception:
                shared_panos = []

        workspace_panos = []
        if workspace_ids:
            try:
                workspace_panos = _select_panorama_rows(sb, 'workspace_id', workspace_ids)
            except Exception:
                workspace_panos = []

        by_id = {}
        access_by_id = {}

        def _upsert(row, access_type):
            pid = _normalize_panorama_id(row.get('id')) if isinstance(row, dict) else None
            if pid is None:
                return
            existing_access = access_by_id.get(pid)
            new_access = _normalize_access_type(access_type)
            if existing_access is None or _access_rank(new_access) > _access_rank(existing_access):
                access_by_id[pid] = new_access
            if pid not in by_id:
                by_id[pid] = dict(row)

        for row in (owned.data or []):
            _upsert(row, 'owner')

        for row in (shared_panos or []):
            pid = row.get('id') if isinstance(row, dict) else None
            _upsert(row, shared_access.get(pid, 'viewer'))

        for row in (workspace_panos or []):
            wsid = row.get('workspace_id') if isinstance(row, dict) else None
            access_type = workspace_to_access.get(str(wsid)) if wsid else None
            if not access_type:
                continue
            _upsert(row, access_type)

        panorama_ids = list(by_id.keys())
        plot_counts = _plot_counts_for_panoramas(sb, panorama_ids)

        result = []
        for pid, row in by_id.items():
            out = dict(row)
            out['access_type'] = access_by_id.get(pid, 'viewer')
            out['plot_count'] = int(plot_counts.get(pid, 0))
            result.append(out)

        result.sort(key=lambda x: x.get('updated_at') or '', reverse=True)
        return result
    except Exception:
        return []


def _count_plots(sb, panorama_id):
    try:
        r = sb.table('plots').select('id', count='exact').eq('panorama_id', panorama_id).execute()
        return r.count or 0
    except Exception:
        return 0


def can_edit_plots(access_type):
    return access_type in ('owner', 'client')


def can_delete_panorama(access_type):
    return access_type == 'owner'


def get_panorama_by_id(sb, panorama_id):
    """Fetch panorama by id (no access check). For page render. Excludes image_data blob."""
    try:
        cols = 'id, user_id, name, filename, original_filename, width, height, is_360, created_at, updated_at'
        try:
            r = sb.table('panoramas').select(cols + ', workspace_id, use_animated_icons').eq('id', panorama_id).limit(1).execute()
        except Exception:
            try:
                r = sb.table('panoramas').select(cols + ', workspace_id').eq('id', panorama_id).limit(1).execute()
            except Exception:
                r = sb.table('panoramas').select(cols).eq('id', panorama_id).limit(1).execute()
        if r.data and len(r.data) > 0:
            row = r.data[0]
            if 'use_animated_icons' not in row:
                row['use_animated_icons'] = False
            return row
    except Exception:
        pass
    return None
