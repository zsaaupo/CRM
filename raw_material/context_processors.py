from account.permissions import has_role_permissions
from warehouse.scope import has_warehouse_scope

from .page_views import RAW_MATERIAL_CREATE_PERMISSIONS


def raw_material_navigation(request):
    user = request.user
    can_view = has_warehouse_scope(user) and has_role_permissions(user, ('raw_material.view_rawmaterial',))
    return {'raw_material_access': {
        'view': can_view,
        'add': can_view and has_role_permissions(user, RAW_MATERIAL_CREATE_PERMISSIONS),
    }}
