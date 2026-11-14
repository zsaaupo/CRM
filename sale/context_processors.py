from .page_views import SALES_CREATE_PERMISSIONS


def sale_navigation(request):
    user = request.user
    can_view = user.is_authenticated and user.has_perm('sale.view_salesorder')
    return {'sale_access': {
        'view': can_view,
        'add': can_view and user.has_perms(SALES_CREATE_PERMISSIONS),
    }}
