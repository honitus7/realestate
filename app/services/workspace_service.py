"""
Workspace: CRUD, access, main_panorama_id. Helpers for serialization and permissions.
"""
from datetime import datetime

from app.core.auth import get_profile
from app.services.panorama_service import get_panorama_by_id, list_panoramas_for_user


def _is_workspace_schema_missing(exc):
    msg = str(exc).lower()
    if 'workspace_id' in msg and ('column' in msg or 'does not exist' in msg):
        return True
    if 'workspaces' in msg and ('relation' in msg or 'does not exist' in msg):
        return True
    if 'workspace_access' in msg and ('relation' in msg or 'does not exist' in msg):
        return True
    return False


def serialize_workspace_row(row, access_type='owner'):
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
    if out.get('main_panorama_id') is not None:
        try:
            out['main_panorama_id'] = int(out['main_panorama_id'])
        except (TypeError, ValueError):
            out['main_panorama_id'] = None
    else:
        out['main_panorama_id'] = None
    out['access_type'] = access_type
    return out


def get_workspace_by_id(sb, workspace_id):
    r = sb.table('workspaces').select('*').eq('id', workspace_id).limit(1).execute()
    if r.data and len(r.data) > 0:
        return dict(r.data[0])
    return None


def clear_workspace_main_for_panorama(sb, panorama_id):
    try:
        sb.table('workspaces').update({
            'main_panorama_id': None,
            'updated_at': datetime.utcnow().isoformat()
        }).eq('main_panorama_id', panorama_id).execute()
    except Exception:
        pass


def can_manage_workspace(sb, workspace, user_id, role):
    if not workspace:
        return False
    if str(workspace.get('user_id') or '') == str(user_id):
        return True
    if role not in ('admin', 'superadmin'):
        return False
    if role == 'superadmin':
        return True
    try:
        caller = get_profile(sb, user_id) or {}
        caller_org = caller.get('org_id')
    except Exception:
        caller_org = None
    workspace_org = workspace.get('org_id')
    return bool(caller_org and workspace_org and str(caller_org) == str(workspace_org))


def list_workspaces(sb, user_id):
    """Return list of workspaces (owned + shared) with panorama_count. Raises on schema error."""
    owned_cols = 'id, user_id, org_id, name, created_at, updated_at'
    try:
        owned = sb.table('workspaces').select(owned_cols + ', main_panorama_id').eq('user_id', user_id).order('updated_at', desc=True).execute()
    except Exception:
        owned = sb.table('workspaces').select(owned_cols).eq('user_id', user_id).order('updated_at', desc=True).execute()

    shared_acc = sb.table('workspace_access').select('workspace_id, access_type').eq('user_id', user_id).execute()
    out = []
    by_id = {}
    for row in (owned.data or []):
        item = serialize_workspace_row(row, 'owner')
        by_id[item['id']] = item
        out.append(item)

    shared_map = {}
    for row in (shared_acc.data or []):
        wsid = row.get('workspace_id')
        if not wsid or str(wsid) in by_id:
            continue
        shared_map[str(wsid)] = str(row.get('access_type') or 'viewer')

    if shared_map:
        shared_cols = 'id, user_id, org_id, name, created_at, updated_at'
        try:
            shared_rows = sb.table('workspaces').select(shared_cols + ', main_panorama_id').in_('id', list(shared_map.keys())).execute()
        except Exception:
            shared_rows = sb.table('workspaces').select(shared_cols).in_('id', list(shared_map.keys())).execute()
        for row in (shared_rows.data or []):
            wsid = str(row.get('id'))
            access_type = shared_map.get(wsid, 'viewer')
            item = serialize_workspace_row(row, access_type)
            by_id[wsid] = item
            out.append(item)

    counts = {}
    for pano in (list_panoramas_for_user(sb, user_id) or []):
        wsid = pano.get('workspace_id') if isinstance(pano, dict) else None
        if not wsid:
            continue
        key = str(wsid)
        counts[key] = int(counts.get(key, 0)) + 1
    for row in out:
        row['panorama_count'] = int(counts.get(str(row.get('id')), 0))

    out = [row for row in out if str(row.get('access_type') or 'viewer') == 'owner' or int(row.get('panorama_count') or 0) > 0]
    out.sort(key=lambda x: ((x.get('access_type') != 'owner'), str(x.get('name') or '').lower()))
    return out


def create_workspace(sb, user_id, name):
    if not name or len(name) > 120:
        raise ValueError('name required and max 120 chars')
    now = datetime.utcnow().isoformat()
    org_id = None
    try:
        profile = get_profile(sb, user_id) or {}
        org_id = profile.get('org_id')
    except Exception:
        pass
    row = {
        'user_id': user_id,
        'org_id': org_id,
        'name': name,
        'created_at': now,
        'updated_at': now,
    }
    r = sb.table('workspaces').insert(row).execute()
    created = (r.data or [None])[0]
    return serialize_workspace_row(created or row, 'owner')


def update_workspace(sb, workspace_id, user_id, name=None, main_panorama_id=None):
    workspace = get_workspace_by_id(sb, workspace_id)
    if not workspace:
        return None
    if str(workspace.get('user_id') or '') != str(user_id):
        return None  # caller should 403
    update_fields = {'updated_at': datetime.utcnow().isoformat()}
    if name is not None:
        if len(name) > 120:
            raise ValueError('name max 120 chars')
        update_fields['name'] = name
    if main_panorama_id is not None:
        if main_panorama_id <= 0:
            update_fields['main_panorama_id'] = None
        else:
            pano = get_panorama_by_id(sb, main_panorama_id)
            if not pano or str(pano.get('workspace_id') or '') != str(workspace_id):
                raise ValueError('Panorama not in workspace')
            if not bool(pano.get('is_360')):
                raise ValueError('Main must be 360')
            update_fields['main_panorama_id'] = main_panorama_id
    sb.table('workspaces').update(update_fields).eq('id', workspace_id).execute()
    merged = dict(workspace)
    merged.update(update_fields)
    return serialize_workspace_row(merged, 'owner')


def delete_workspace(sb, workspace_id, user_id):
    workspace = get_workspace_by_id(sb, workspace_id)
    if not workspace or str(workspace.get('user_id') or '') != str(user_id):
        return False
    sb.table('workspaces').delete().eq('id', workspace_id).execute()
    return True


def get_workspace_schema_error_response():
    return {'error': 'Workspace schema missing. Run supabase_migration_workspaces.sql in Supabase SQL Editor.'}, 503


def is_workspace_schema_missing(exc):
    return _is_workspace_schema_missing(exc)
