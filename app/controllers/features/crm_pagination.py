from flask import request


def crm_parse_page_args(*, default_limit=10, max_limit=100):
    try:
        limit = int(request.args.get('limit', default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    limit = max(1, min(max_limit, limit))
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)
    offset = (page - 1) * limit
    return page, limit, offset


def crm_page_payload(items, total, page, limit):
    total = int(total or 0)
    pages = max(1, (total + limit - 1) // limit) if limit else 1
    return {
        'items': items or [],
        'total': total,
        'page': page,
        'limit': limit,
        'pages': pages,
    }
