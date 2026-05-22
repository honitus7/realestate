from datetime import datetime

from flask import jsonify, request

from app import config as app_config
from app.core.auth import require_auth
from app.core.database import get_supabase
from app.services.email_service import send_email as send_smtp_email


def register_crm_quote_routes(
    app,
    *,
    crm_panorama_ids,
    crm_client_scope_ids,
    crm_apply_client_scope,
    get_contact_for_user,
    crm_cache_bump,
):
    @app.route('/api/crm/deals/<deal_id>/quotation/share', methods=['POST'])
    @require_auth
    def share_deal_quote(user_id, role, deal_id):
        sb = get_supabase()
        if not sb:
            return jsonify({'error': 'Database not configured'}), 503
        panorama_ids = crm_panorama_ids(sb, user_id, role)
        if not panorama_ids:
            return jsonify({'error': 'Forbidden'}), 403
        client_scope_ids = crm_client_scope_ids(sb, user_id, role)
        data = request.get_json(silent=True) or {}
        quote_id = str(data.get('quote_id') or '').strip()
        if not quote_id:
            return jsonify({'error': 'quote_id is required'}), 400
        qr = (
            sb.table('crm_deal_quotes')
            .select('id, deal_id, contact_id, quote_payload, share_token')
            .eq('id', quote_id)
            .eq('deal_id', str(deal_id))
            .limit(1)
            .execute()
        )
        if not qr.data:
            return jsonify({'error': 'Quote not found'}), 404
        quote = qr.data[0]
        drq = (
            sb.table('crm_deals')
            .select('id, panorama_id, title')
            .eq('id', str(deal_id))
        )
        drq = drq.in_('panorama_id', panorama_ids)
        if client_scope_ids is not None:
            drq = crm_apply_client_scope(drq, client_scope_ids)
            if drq is None:
                return jsonify({'error': 'Deal not found or access denied'}), 404
        dr = drq.limit(1).execute()
        if not dr.data:
            return jsonify({'error': 'Deal not found or access denied'}), 404
        to_email = str(data.get('email') or '').strip()
        to_phone = str(data.get('phone') or '').strip()
        if not to_email and quote.get('contact_id'):
            contact = get_contact_for_user(sb, quote.get('contact_id'), panorama_ids, client_ids=client_scope_ids)
            if contact:
                to_email = str(contact.get('email') or '').strip()
                to_phone = str(contact.get('phone') or '').strip()
        if not to_email:
            return jsonify({'error': 'No recipient email found'}), 400
        share_url = f"{request.url_root.rstrip('/')}/api/crm/deals/{deal_id}/quotation/{quote_id}?token={quote.get('share_token')}"
        subject = f"Quotation for {dr.data[0].get('title') or 'your deal'}"
        body = (
            f"Hello,\n\nPlease review your quotation using the link below:\n{share_url}\n\n"
            f"Shared via MarketoState CRM on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.\n"
        )
        try:
            send_smtp_email(
                smtp_host=app_config.SMTP_HOST,
                smtp_port=app_config.SMTP_PORT,
                smtp_username=app_config.SMTP_USERNAME,
                smtp_password=app_config.SMTP_PASSWORD,
                smtp_use_tls=app_config.SMTP_USE_TLS,
                from_email=app_config.SMTP_FROM_EMAIL,
                from_name=app_config.SMTP_FROM_NAME,
                to_email=to_email,
                subject=subject,
                text_body=body,
            )
        except Exception as e:
            return jsonify({'error': f'Email send failed: {e}'}), 500
        now = datetime.utcnow().isoformat()
        sb.table('crm_deal_quotes').update({
            'sent_to_email': to_email,
            'sent_to_phone': to_phone,
            'shared_via': 'email',
            'sent_at': now,
            'updated_at': now,
            'status': 'sent',
        }).eq('id', quote_id).execute()
        crm_cache_bump()
        return jsonify({'success': True, 'share_url': share_url})
