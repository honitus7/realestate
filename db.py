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


def ensure_profile(sb, user_id, role='user'):
    """Insert or update profile with role."""
    existing = get_profile(sb, user_id)
    if existing:
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
        if role != 'admin':
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
        return None, None
    except Exception:
        return None, None


# Columns for panorama list/detail (exclude image_data blob)
_PANORAMA_FIELDS = 'id, user_id, name, filename, original_filename, width, height, is_360, created_at, updated_at'


def list_panoramas_for_user(sb, user_id):
    """
    Return list of panoramas the user can see, each with access_type ('owner'|'client'|'viewer') and plot_count.
    """
    try:
        # Owned
        owned = sb.table('panoramas').select(_PANORAMA_FIELDS).eq('user_id', user_id).order('updated_at', desc=True).execute()
        # Shared via panorama_access
        shared = sb.table('panorama_access').select('panorama_id, access_type').eq('user_id', user_id).execute()
        shared_ids = {row['panorama_id']: row['access_type'] for row in (shared.data or [])}
        seen = set()
        result = []
        for p in (owned.data or []):
            seen.add(p['id'])
            plot_count = _count_plots(sb, p['id'])
            result.append({**p, 'access_type': 'owner', 'plot_count': plot_count})
        for pid, acc_type in shared_ids.items():
            if pid in seen:
                continue
            r = sb.table('panoramas').select(_PANORAMA_FIELDS).eq('id', pid).limit(1).execute()
            if r.data and len(r.data) > 0:
                p = r.data[0]
                seen.add(pid)
                plot_count = _count_plots(sb, pid)
                result.append({**p, 'access_type': acc_type, 'plot_count': plot_count})
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
        r = sb.table('panoramas').select(
            'id, user_id, name, filename, original_filename, width, height, is_360, created_at, updated_at'
        ).eq('id', panorama_id).limit(1).execute()
        if r.data and len(r.data) > 0:
            return r.data[0]
    except Exception:
        pass
    return None
