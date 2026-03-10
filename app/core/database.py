"""
Supabase client (service role).
"""
import os

from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

_sb_client = None
_httpx_client = None


def _get_httpx_client():
    """Lazy httpx client for Auth API."""
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
