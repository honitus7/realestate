from .crm import register_crm_quote_routes
from .daynight import register_daynight_routes
from .full_view import register_full_view_routes
from .gallery import register_gallery_routes
from .page_access import register_page_access_routes
from .sales_route_maps import register_sales_route_map_routes
from .uam import register_uam_routes

__all__ = [
    'register_crm_quote_routes',
    'register_daynight_routes',
    'register_full_view_routes',
    'register_gallery_routes',
    'register_page_access_routes',
    'register_sales_route_map_routes',
    'register_uam_routes',
]
