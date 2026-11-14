from .page_views import PURCHASE_CREATE_PERMISSIONS


def purchase_navigation(request):
    user = request.user
    can_view = user.is_authenticated and user.has_perm('purchase.view_purchaseorder')
    return {'purchase_access': {
        'view': can_view,
        'add': can_view and user.has_perms(PURCHASE_CREATE_PERMISSIONS),
    }}
