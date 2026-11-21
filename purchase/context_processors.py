from account.permissions import has_role_permissions
from warehouse.scope import has_warehouse_scope
from .page_views import PURCHASE_CREATE_PERMISSIONS
from .returns import RETURN_VIEW


def purchase_navigation(request):
    user = request.user
    scoped = has_warehouse_scope(user)
    can_view = scoped and has_role_permissions(user, ('purchase.view_purchaseorder',))
    return {'purchase_access': {
        'view': can_view,
        'add': can_view and has_role_permissions(user, PURCHASE_CREATE_PERMISSIONS),
        'returns': scoped and has_role_permissions(user, RETURN_VIEW),
    }}
