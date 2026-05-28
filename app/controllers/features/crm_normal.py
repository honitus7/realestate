from flask import jsonify, request

from app.core.auth import require_auth
from app.core.database import get_supabase
from app.controllers.features.crm_pagination import crm_page_payload, crm_parse_page_args


_NORMAL_PROJECT_SELECT_EXT = (
    'id, owner_user_id, name, location, description, project_type, '
    'builder_name, location_address, location_city, location_state, '
    'google_maps_link, location_lat, location_lng, rera_registration, total_area, '
    'launch_date, possession_date, contact_phone, contact_email, amenities, custom_fields, '
    'created_at, updated_at'
)


def register_crm_normal_routes(app):
    @app.route('/api/crm/normal-projects', methods=['GET'])
    @require_auth
    def list_crm_normal_projects(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        page, limit, offset = crm_parse_page_args(default_limit=10, max_limit=100)
        q = str(request.args.get('q') or '').strip().lower()
        query = (
            sb.table('crm_normal_projects')
            .select(_NORMAL_PROJECT_SELECT_EXT, count='exact')
            .eq('owner_user_id', str(user_id))
            .order('updated_at', desc=True)
        )
        if q:
            query = query.or_(f'name.ilike.%{q}%,location.ilike.%{q}%')
        try:
            result = query.range(offset, offset + limit - 1).execute()
        except Exception as e:
            msg = str(e or '')
            if 'column' in msg.lower() and 'does not exist' in msg.lower():
                fallback_query = (
                    sb.table('crm_normal_projects')
                    .select('id, owner_user_id, name, location, description, project_type, created_at, updated_at', count='exact')
                    .eq('owner_user_id', str(user_id))
                    .order('updated_at', desc=True)
                )
                if q:
                    fallback_query = fallback_query.or_(f'name.ilike.%{q}%,location.ilike.%{q}%')
                result = fallback_query.range(offset, offset + limit - 1).execute()
            else:
                raise
        items = result.data or []
        for item in items:
            item['crm_mode'] = 'normal'
        total = int(result.count or 0)
        return jsonify(crm_page_payload(items, total, page, limit))

    @app.route('/api/crm/normal-projects', methods=['POST'])
    @require_auth
    def create_crm_normal_project(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Project name is required'}), 400
        row = {
            'owner_user_id': str(user_id),
            'name': name,
            'location': str(data.get('location') or '').strip() or None,
            'description': str(data.get('description') or '').strip() or None,
            'project_type': str(data.get('project_type') or '').strip() or 'residential',
            'builder_name': str(data.get('builder_name') or '').strip() or None,
            'location_address': str(data.get('location_address') or data.get('location') or '').strip() or None,
            'location_city': str(data.get('location_city') or '').strip() or None,
            'location_state': str(data.get('location_state') or '').strip() or None,
            'google_maps_link': str(data.get('google_maps_link') or '').strip() or None,
            'location_lat': data.get('location_lat') if data.get('location_lat') is not None else None,
            'location_lng': data.get('location_lng') if data.get('location_lng') is not None else None,
            'rera_registration': str(data.get('rera_registration') or '').strip() or None,
            'total_area': str(data.get('total_area') or '').strip() or None,
            'launch_date': data.get('launch_date') or None,
            'possession_date': str(data.get('possession_date') or '').strip() or None,
            'contact_phone': str(data.get('contact_phone') or '').strip() or None,
            'contact_email': str(data.get('contact_email') or '').strip() or None,
            'amenities': data.get('amenities') if isinstance(data.get('amenities'), list) else [],
            'custom_fields': data.get('custom_fields') if isinstance(data.get('custom_fields'), dict) else {},
        }
        try:
            created = sb.table('crm_normal_projects').insert(row).execute().data or []
        except Exception as e:
            msg = str(e or '')
            if 'column' in msg.lower() and 'does not exist' in msg.lower():
                legacy_row = {
                    'owner_user_id': str(user_id),
                    'name': name,
                    'location': str(data.get('location') or data.get('location_address') or '').strip() or None,
                    'description': str(data.get('description') or '').strip() or None,
                    'project_type': str(data.get('project_type') or '').strip() or 'residential',
                }
                created = sb.table('crm_normal_projects').insert(legacy_row).execute().data or []
            else:
                raise
        out = (created[0] if created else row)
        out['crm_mode'] = 'normal'
        return jsonify(out), 201

    @app.route('/api/crm/normal-plots', methods=['GET'])
    @require_auth
    def list_crm_normal_plots(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        page, limit, offset = crm_parse_page_args(default_limit=10, max_limit=100)
        q = str(request.args.get('q') or '').strip().lower()
        project_id = str(request.args.get('project_id') or '').strip()
        query = (
            sb.table('crm_normal_plots')
            .select('id, owner_user_id, project_id, project_name, name, area, price, status, description, panorama_id, custom_fields, created_at, updated_at', count='exact')
            .eq('owner_user_id', str(user_id))
            .order('updated_at', desc=True)
        )
        if project_id:
            query = query.eq('project_id', project_id)
        if q:
            query = query.or_(f'name.ilike.%{q}%,project_name.ilike.%{q}%,status.ilike.%{q}%')
        result = query.range(offset, offset + limit - 1).execute()
        items = result.data or []
        for item in items:
            item['crm_mode'] = 'normal'
            if item.get('panorama_id') is None:
                item['panorama_id'] = 'none'
        total = int(result.count or 0)
        return jsonify(crm_page_payload(items, total, page, limit))

    @app.route('/api/crm/normal-plots', methods=['POST'])
    @require_auth
    def create_crm_normal_plot(user_id, role):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        data = request.get_json(silent=True) or {}
        name = str(data.get('name') or '').strip()
        project_name = str(data.get('project_name') or '').strip()
        if not name:
            return jsonify({'error': 'Plot name is required'}), 400
        if not project_name:
            return jsonify({'error': 'Project name is required'}), 400
        row = {
            'owner_user_id': str(user_id),
            'project_id': data.get('project_id') or None,
            'project_name': project_name,
            'name': name,
            'area': str(data.get('area') or '').strip() or None,
            'price': str(data.get('price') or '').strip() or None,
            'status': str(data.get('status') or '').strip() or 'available',
            'description': str(data.get('description') or '').strip() or None,
            'panorama_id': None,
            'custom_fields': data.get('custom_fields') if isinstance(data.get('custom_fields'), dict) else {},
        }
        created = sb.table('crm_normal_plots').insert(row).execute().data or []
        out = (created[0] if created else row)
        out['crm_mode'] = 'normal'
        out['panorama_id'] = 'none'
        return jsonify(out), 201
