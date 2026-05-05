"""
Sales route map CRUD operations against Supabase.
This module powers map-image uploads, pointers, and drawable routes.
"""
import uuid
import json
from postgrest.exceptions import APIError


def _generate_share_token():
    return uuid.uuid4().hex[:12]


def _default_marker_icon(marker_type):
    return 'main-star' if str(marker_type or '').strip().lower() == 'main' else 'plot-pin'


def _normalize_marker_icon(marker):
    if not isinstance(marker, dict):
        return marker
    icon_key = str(marker.get('icon_key') or '').strip().lower()
    marker['icon_key'] = icon_key or _default_marker_icon(marker.get('marker_type'))
    return marker


def _is_missing_marker_icon_column_error(exc):
    payload = {}
    raw = exc.args[0] if getattr(exc, 'args', None) else {}
    if isinstance(raw, dict):
        payload = raw
    code = str(payload.get('code') or '')
    message = str(payload.get('message') or str(exc))
    if code and code != 'PGRST204':
        return False
    return "'icon_key'" in message and "'sales_route_map_markers'" in message


# ---------------------------------------------------------------------------
# Sales map CRUD
# ---------------------------------------------------------------------------


def create_sales_map(sb, user_id, org_id, name, image_filename, image_width=0, image_height=0, workspace_id=None):
    row = {
        'user_id': str(user_id),
        'name': name,
        'image_filename': image_filename,
        'image_width': int(image_width or 0),
        'image_height': int(image_height or 0),
        'share_token': _generate_share_token(),
    }
    if org_id:
        row['org_id'] = str(org_id)
    if workspace_id:
        row['workspace_id'] = str(workspace_id)
    resp = sb.table('sales_route_maps').insert(row).execute()
    data = resp.data
    return data[0] if data else None


def get_sales_map(sb, map_id):
    resp = sb.table('sales_route_maps').select('*').eq('id', str(map_id)).execute()
    data = resp.data
    return data[0] if data else None


def get_sales_map_by_token(sb, token):
    resp = sb.table('sales_route_maps').select('*').eq('share_token', str(token)).execute()
    data = resp.data
    return data[0] if data else None


def list_sales_maps(sb, user_id, workspace_id=None):
    q = (
        sb.table('sales_route_maps')
        .select('*')
        .eq('user_id', str(user_id))
        .order('created_at', desc=True)
    )
    if workspace_id:
        q = q.eq('workspace_id', str(workspace_id))
    resp = q.execute()
    return resp.data or []


def update_sales_map(sb, map_id, **fields):
    clean = {}
    for k, v in fields.items():
        if k in ('name', 'workspace_id'):
            clean[k] = v
    if not clean:
        return None
    resp = sb.table('sales_route_maps').update(clean).eq('id', str(map_id)).execute()
    data = resp.data
    return data[0] if data else None


def delete_sales_map(sb, map_id):
    smap = get_sales_map(sb, map_id)
    if not smap:
        return None
    sb.table('sales_route_maps').delete().eq('id', str(map_id)).execute()
    return smap


# ---------------------------------------------------------------------------
# Marker CRUD
# ---------------------------------------------------------------------------


def list_markers(sb, map_id):
    resp = (
        sb.table('sales_route_map_markers')
        .select('*')
        .eq('map_id', str(map_id))
        .order('sort_order')
        .order('created_at')
        .execute()
    )
    markers = resp.data or []
    return [_normalize_marker_icon(marker) for marker in markers]


def get_marker(sb, marker_id):
    resp = sb.table('sales_route_map_markers').select('*').eq('id', str(marker_id)).execute()
    data = resp.data
    return _normalize_marker_icon(data[0]) if data else None


def get_next_marker_sort_order(sb, map_id):
    markers = list_markers(sb, map_id)
    if not markers:
        return 0
    return max(int(m.get('sort_order', 0) or 0) for m in markers) + 1


def clear_main_marker(sb, map_id, exclude_marker_id=None):
    q = (
        sb.table('sales_route_map_markers')
        .update({'marker_type': 'normal'})
        .eq('map_id', str(map_id))
        .eq('marker_type', 'main')
    )
    if exclude_marker_id:
        q = q.neq('id', str(exclude_marker_id))
    q.execute()
    return True


def create_marker(sb, map_id, marker_type, label, x_ratio, y_ratio, icon_key='', sort_order=0):
    row = {
        'map_id': str(map_id),
        'marker_type': str(marker_type or 'normal'),
        'label': str(label or '').strip(),
        'icon_key': str(icon_key or '').strip().lower(),
        'x_ratio': float(x_ratio),
        'y_ratio': float(y_ratio),
        'sort_order': int(sort_order or 0),
    }
    try:
        resp = sb.table('sales_route_map_markers').insert(row).execute()
    except APIError as exc:
        if not _is_missing_marker_icon_column_error(exc):
            raise
        legacy_row = dict(row)
        legacy_row.pop('icon_key', None)
        resp = sb.table('sales_route_map_markers').insert(legacy_row).execute()
    data = resp.data
    return _normalize_marker_icon(data[0]) if data else None


def update_marker(sb, marker_id, **fields):
    clean = {}
    for k, v in fields.items():
        if k in ('marker_type', 'label', 'sort_order', 'icon_key'):
            clean[k] = v
        elif k in ('x_ratio', 'y_ratio'):
            clean[k] = float(v)
    if not clean:
        return None
    try:
        resp = sb.table('sales_route_map_markers').update(clean).eq('id', str(marker_id)).execute()
    except APIError as exc:
        if not _is_missing_marker_icon_column_error(exc) or 'icon_key' not in clean:
            raise
        legacy_clean = dict(clean)
        legacy_clean.pop('icon_key', None)
        if not legacy_clean:
            return get_marker(sb, marker_id)
        resp = sb.table('sales_route_map_markers').update(legacy_clean).eq('id', str(marker_id)).execute()
    data = resp.data
    return _normalize_marker_icon(data[0]) if data else None


def delete_marker(sb, marker_id):
    marker = get_marker(sb, marker_id)
    if not marker:
        return None
    sb.table('sales_route_map_markers').delete().eq('id', str(marker_id)).execute()
    return marker


# ---------------------------------------------------------------------------
# Route CRUD
# ---------------------------------------------------------------------------


def _normalize_points(points):
    if isinstance(points, str):
        try:
            parsed = json.loads(points)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return points if isinstance(points, list) else []


def list_routes(sb, map_id):
    resp = (
        sb.table('sales_route_map_routes')
        .select('*')
        .eq('map_id', str(map_id))
        .order('sort_order')
        .order('created_at')
        .execute()
    )
    routes = resp.data or []
    for route in routes:
        route['path_points'] = _normalize_points(route.get('path_points'))
    return routes


def get_route(sb, route_id):
    resp = sb.table('sales_route_map_routes').select('*').eq('id', str(route_id)).execute()
    data = resp.data
    if not data:
        return None
    route = data[0]
    route['path_points'] = _normalize_points(route.get('path_points'))
    return route


def get_next_route_sort_order(sb, map_id):
    routes = list_routes(sb, map_id)
    if not routes:
        return 0
    return max(int(r.get('sort_order', 0) or 0) for r in routes) + 1


def create_route(sb, map_id, from_marker_id, to_marker_id, path_points, color='#162338', line_width=3, sort_order=0):
    row = {
        'map_id': str(map_id),
        'from_marker_id': str(from_marker_id),
        'to_marker_id': str(to_marker_id),
        'path_points': json.dumps(_normalize_points(path_points)),
        'color': str(color or '#162338'),
        'line_width': int(line_width or 3),
        'sort_order': int(sort_order or 0),
    }
    resp = sb.table('sales_route_map_routes').insert(row).execute()
    data = resp.data
    if not data:
        return None
    route = data[0]
    route['path_points'] = _normalize_points(route.get('path_points'))
    return route


def update_route(sb, route_id, **fields):
    clean = {}
    for k, v in fields.items():
        if k in ('from_marker_id', 'to_marker_id', 'color'):
            clean[k] = str(v) if v is not None else None
        elif k in ('line_width', 'sort_order'):
            clean[k] = int(v)
        elif k == 'path_points':
            clean[k] = json.dumps(_normalize_points(v))
    if not clean:
        return None
    resp = sb.table('sales_route_map_routes').update(clean).eq('id', str(route_id)).execute()
    data = resp.data
    if not data:
        return None
    route = data[0]
    route['path_points'] = _normalize_points(route.get('path_points'))
    return route


def delete_route(sb, route_id):
    route = get_route(sb, route_id)
    if not route:
        return None
    sb.table('sales_route_map_routes').delete().eq('id', str(route_id)).execute()
    return route
