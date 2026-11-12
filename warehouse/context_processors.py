def warehouse_navigation(request):
    user = request.user
    can_view = user.is_authenticated and user.has_perm('warehouse.view_warehouse')
    return {'warehouse_access': {
        'view': can_view,
        'add': can_view and user.has_perm('warehouse.add_warehouse'),
    }}
