"""
Auth: JWT verification, profiles, decorators.
"""
from functools import wraps
from datetime import datetime

from flask import request, jsonify

from app.config import SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_JWT_SECRET
from app.core.database import get_supabase, _get_httpx_client

try:
    import jwt
except ImportError:
    jwt = None


def get_current_user():
    """
    From Authorization: Bearer <jwt>, verify and return (user_id, role) or (None, None).
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
    """Backfill profile display_name and email from Supabase Auth user_metadata."""
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
    """Decorator: require valid JWT; inject (user_id, role) as first args."""
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
