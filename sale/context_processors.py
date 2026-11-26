from account.permissions import has_role_permissions
from warehouse.scope import has_warehouse_scope
from .page_views import SALES_CREATE_PERMISSIONS
from .returns import RETURN_VIEW
from .services import QUOTE_VIEW
from .pos import POS_PERMS


def sale_navigation(request):
    user = request.user
    scoped = has_warehouse_scope(user)
    can_view = scoped and has_role_permissions(user, ('sale.view_salesorder',))
    return {'sale_access': {
        'view': can_view,
        'pos': scoped and has_role_permissions(user, POS_PERMS),
        'coupons': has_role_permissions(user, ('sale.view_coupon',)),
        'gift_cards': has_role_permissions(user, ('sale.view_giftcard',)),
        'add': can_view and has_role_permissions(user, SALES_CREATE_PERMISSIONS),
        'returns': scoped and has_role_permissions(user, RETURN_VIEW),
        'quotations': scoped and has_role_permissions(user, QUOTE_VIEW),
    }}
