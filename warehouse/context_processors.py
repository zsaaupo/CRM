from account.permissions import has_role_permissions
from .scope import all_warehouse_access, has_warehouse_scope, warehouse_scope


def warehouse_navigation(request):
    user = request.user
    scoped = has_warehouse_scope(user)
    can_view = scoped and has_role_permissions(user, ('warehouse.view_warehouse',))
    can_view_stock = scoped and has_role_permissions(user, ('warehouse.view_stock',))
    return {'warehouse_access': {
        'view': can_view,
        'stock': can_view_stock,
        'add': can_view and has_role_permissions(user, ('warehouse.add_warehouse',)) and all_warehouse_access(warehouse_scope(user)),
    }}
