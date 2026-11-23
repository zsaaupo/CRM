from account.permissions import has_role_permissions
from warehouse.scope import has_warehouse_scope

from .services import ACCOUNT_VIEW, EXPENSE_VIEW, STATEMENT_VIEW, TRANSFER_VIEW


def expense_navigation(request):
    user = request.user
    scoped = has_warehouse_scope(user)
    return {
        'expense_access': {
            'categories': user.has_perm('accounting.view_expensecategory'),
            'expenses': scoped and has_role_permissions(user, (EXPENSE_VIEW,)),
        },
        'finance_access': {
            'accounts': scoped and has_role_permissions(user, (ACCOUNT_VIEW,)),
            'cash_flow': scoped and has_role_permissions(user, STATEMENT_VIEW),
            'transfers': scoped and has_role_permissions(user, TRANSFER_VIEW),
        },
    }
