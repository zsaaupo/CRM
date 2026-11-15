def warehouse_navigation(request):
    user = request.user
    can_view = user.is_authenticated and user.has_perm('warehouse.view_warehouse')
    can_view_stock = user.is_authenticated and user.has_perm('warehouse.view_stock')
    return {'warehouse_access': {
        'view': can_view,
        'stock': can_view_stock,
        'add': can_view and user.has_perm('warehouse.add_warehouse'),
    }}
