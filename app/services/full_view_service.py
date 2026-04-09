"""
Full View Creator - CRUD for full_view_configs and full_view_tabs.
"""


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------

def get_config_for_workspace(sb, workspace_id):
    resp = (
        sb.table('full_view_configs')
        .select('*')
        .eq('workspace_id', str(workspace_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def create_config(sb, workspace_id, user_id, org_id=None):
    row = {
        'workspace_id': str(workspace_id),
        'user_id': str(user_id),
        'is_active': True,
    }
    if org_id:
        row['org_id'] = str(org_id)
    resp = sb.table('full_view_configs').insert(row).execute()
    return resp.data[0] if resp.data else None


def update_config(sb, config_id, **fields):
    clean = {k: v for k, v in fields.items() if k in {'is_active'}}
    if not clean:
        return None
    resp = (
        sb.table('full_view_configs')
        .update(clean)
        .eq('id', str(config_id))
        .execute()
    )
    return resp.data[0] if resp.data else None


def delete_config(sb, config_id):
    sb.table('full_view_configs').delete().eq('id', str(config_id)).execute()
    return True


# ---------------------------------------------------------------------------
# Tab CRUD
# ---------------------------------------------------------------------------

_TAB_WRITABLE = {
    'icon', 'name', 'tab_type', 'is_visible', 'sort_order', 'content_data',
    'ref_panorama_id', 'ref_daynight_id', 'ref_floor_plan_id',
    'ref_gallery_id', 'ref_project_plan_id',
}


def list_tabs(sb, config_id, visible_only=False):
    q = (
        sb.table('full_view_tabs')
        .select('*')
        .eq('config_id', str(config_id))
        .order('sort_order', desc=False)
    )
    if visible_only:
        q = q.eq('is_visible', True)
    resp = q.execute()
    return resp.data or []


def create_tab(sb, config_id, icon, name, tab_type, **kwargs):
    row = {
        'config_id': str(config_id),
        'icon': icon,
        'name': name,
        'tab_type': tab_type,
        'is_visible': kwargs.get('is_visible', True),
        'sort_order': kwargs.get('sort_order', 0),
        'content_data': kwargs.get('content_data') or {},
    }
    for field in ('ref_panorama_id', 'ref_daynight_id', 'ref_floor_plan_id',
                  'ref_gallery_id', 'ref_project_plan_id'):
        val = kwargs.get(field)
        if val is not None:
            row[field] = val
    resp = sb.table('full_view_tabs').insert(row).execute()
    return resp.data[0] if resp.data else None


def update_tab(sb, tab_id, **fields):
    clean = {k: v for k, v in fields.items() if k in _TAB_WRITABLE}
    if not clean:
        return None
    # Nullify all ref fields when switching types so stale refs don't persist
    if 'tab_type' in clean:
        for f in ('ref_panorama_id', 'ref_daynight_id', 'ref_floor_plan_id',
                  'ref_gallery_id', 'ref_project_plan_id'):
            if f not in clean:
                clean[f] = None
    resp = (
        sb.table('full_view_tabs')
        .update(clean)
        .eq('id', str(tab_id))
        .execute()
    )
    return resp.data[0] if resp.data else None


def delete_tab(sb, tab_id):
    sb.table('full_view_tabs').delete().eq('id', str(tab_id)).execute()
    return True


def reorder_tabs(sb, config_id, tab_ids):
    """Bulk-update sort_order based on position in tab_ids list."""
    for i, tab_id in enumerate(tab_ids):
        sb.table('full_view_tabs').update({'sort_order': i}).eq(
            'id', str(tab_id)
        ).eq('config_id', str(config_id)).execute()
    return True


# ---------------------------------------------------------------------------
# Customer view helper
# ---------------------------------------------------------------------------

def get_config_with_tabs(sb, workspace_id):
    """Return config dict with 'tabs' list (visible only) for the customer view."""
    config = get_config_for_workspace(sb, workspace_id)
    if not config or not config.get('is_active'):
        return None
    tabs = list_tabs(sb, config['id'], visible_only=True)
    return {**config, 'tabs': tabs}
