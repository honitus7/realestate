"""
Floor Plan Catalogue CRUD operations against Supabase.
"""
import uuid


def _generate_share_token():
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Catalogue CRUD
# ---------------------------------------------------------------------------

def create_catalogue(sb, user_id, org_id, name='Floor Plans'):
    token = _generate_share_token()
    row = {
        'user_id': str(user_id),
        'name': name,
        'share_token': token,
    }
    if org_id:
        row['org_id'] = str(org_id)
    resp = sb.table('floor_plan_catalogues').insert(row).execute()
    data = resp.data
    return data[0] if data else None


def get_catalogue(sb, catalogue_id):
    resp = sb.table('floor_plan_catalogues').select('*').eq('id', str(catalogue_id)).execute()
    data = resp.data
    return data[0] if data else None


def get_catalogue_by_token(sb, token):
    resp = sb.table('floor_plan_catalogues').select('*').eq('share_token', str(token)).execute()
    data = resp.data
    return data[0] if data else None


def list_catalogues(sb, user_id):
    resp = (
        sb.table('floor_plan_catalogues')
        .select('*')
        .eq('user_id', str(user_id))
        .order('created_at', desc=True)
        .execute()
    )
    return resp.data or []


def update_catalogue(sb, catalogue_id, **fields):
    clean = {}
    for k, v in fields.items():
        if k in ('name',):
            clean[k] = v
    if not clean:
        return None
    resp = sb.table('floor_plan_catalogues').update(clean).eq('id', str(catalogue_id)).execute()
    data = resp.data
    return data[0] if data else None


def delete_catalogue(sb, catalogue_id):
    cat = get_catalogue(sb, catalogue_id)
    if not cat:
        return None
    sb.table('floor_plan_catalogues').delete().eq('id', str(catalogue_id)).execute()
    return cat


# ---------------------------------------------------------------------------
# Floor Plan Items CRUD
# ---------------------------------------------------------------------------

def list_items(sb, catalogue_id):
    resp = (
        sb.table('floor_plan_items')
        .select('*')
        .eq('catalogue_id', str(catalogue_id))
        .order('sort_order')
        .execute()
    )
    return resp.data or []


def get_item(sb, item_id):
    resp = sb.table('floor_plan_items').select('*').eq('id', str(item_id)).execute()
    data = resp.data
    return data[0] if data else None


def create_item(sb, catalogue_id, name, image_filename, image_width=0, image_height=0, file_size_bytes=0, sort_order=0):
    row = {
        'catalogue_id': str(catalogue_id),
        'name': name,
        'image_filename': image_filename,
        'image_width': image_width,
        'image_height': image_height,
        'file_size_bytes': file_size_bytes,
        'sort_order': sort_order,
    }
    resp = sb.table('floor_plan_items').insert(row).execute()
    data = resp.data
    return data[0] if data else None


def update_item(sb, item_id, **fields):
    clean = {}
    for k, v in fields.items():
        if k in ('name', 'sort_order', 'image_filename', 'image_width', 'image_height', 'file_size_bytes'):
            clean[k] = v
    if not clean:
        return None
    resp = sb.table('floor_plan_items').update(clean).eq('id', str(item_id)).execute()
    data = resp.data
    return data[0] if data else None


def delete_item(sb, item_id):
    item = get_item(sb, item_id)
    if not item:
        return None
    sb.table('floor_plan_items').delete().eq('id', str(item_id)).execute()
    return item


def reorder_items(sb, catalogue_id, ordered_ids):
    """Update sort_order for items based on provided ID list order."""
    for idx, item_id in enumerate(ordered_ids):
        sb.table('floor_plan_items').update({'sort_order': idx}).eq('id', str(item_id)).eq('catalogue_id', str(catalogue_id)).execute()


def get_next_sort_order(sb, catalogue_id):
    items = list_items(sb, catalogue_id)
    if not items:
        return 0
    return max(it.get('sort_order', 0) for it in items) + 1
