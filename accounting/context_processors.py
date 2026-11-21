from account.permissions import has_role_permissions
from warehouse.scope import has_warehouse_scope


def expense_navigation(request):
    user = request.user
    return {'expense_access': {
        'categories': user.has_perm('accounting.view_expensecategory'),
        'expenses': has_role_permissions(user, ('accounting.view_expense',)) and has_warehouse_scope(user),
    }}
