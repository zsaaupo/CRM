from account.permissions import has_role_permissions
from warehouse.scope import has_warehouse_scope
from .page_views import SALES_CREATE_PERMISSIONS


def sale_navigation(request):
    user = request.user
    can_view = has_warehouse_scope(user) and has_role_permissions(user, ('sale.view_salesorder',))
    return {'sale_access': {
        'view': can_view,
        'add': can_view and has_role_permissions(user, SALES_CREATE_PERMISSIONS),
    }}
