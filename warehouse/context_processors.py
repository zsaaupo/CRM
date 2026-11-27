from account.permissions import has_role_permissions
from rest_framework.exceptions import PermissionDenied
from .activity import available_reports
from .scope import all_warehouse_access, warehouse_scope


def warehouse_navigation(request):
    user = request.user
    try:
        scope = warehouse_scope(user)
    except PermissionDenied:
        return {'warehouse_access': dict.fromkeys(('report', 'view', 'stock', 'transfers', 'add_transfer', 'add'), False)}
    can_view = has_role_permissions(user, ('warehouse.view_warehouse',))
    can_view_stock = has_role_permissions(user, ('warehouse.view_stock',))
    return {'warehouse_access': {
        'report': bool(available_reports(scope)),
        'view': can_view,
        'stock': can_view_stock,
        'movements': has_role_permissions(user, ('warehouse.view_stockmovement',)),
        'counts': can_view_stock and has_role_permissions(user, ('warehouse.view_stockcount',)),
        'adjustments': can_view_stock and has_role_permissions(user, ('warehouse.view_stockadjustment',)),
        'adjustments': can_view_stock and has_role_permissions(user, ('warehouse.view_stockadjustment',)),
        'transfers': has_role_permissions(user, ('warehouse.view_warehousetransfer',)),
        'add_transfer': all_warehouse_access(scope) and has_role_permissions(user, ('warehouse.view_warehousetransfer',
                        'warehouse.add_warehousetransfer')),
        'add': can_view and all_warehouse_access(scope) and has_role_permissions(user, ('warehouse.add_warehouse',)),
    }}
