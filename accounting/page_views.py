from uuid import uuid4

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from inventory.catalog import CatalogResource
from sale.returns import completed_orders
from warehouse.models import WareHouse
from warehouse.scope import all_warehouse_access, has_warehouse_scope, scoped_queryset, warehouse_scope

from .models import AccountTransaction, AccountTransfer, Expense, ExpenseCategory, FinancialAccount, SalesPayment
from .services import SALES_PAYMENT_VIEW, STATEMENT_VIEW, TRANSFER_VIEW, accounts_with_balances, sales_payment_totals


CATEGORIES = CatalogResource(
    'expense-category', 'expense-categories', 'expense category', 'expense categories', ExpenseCategory,
    (('name', 'Name', 'text'), ('is_active', 'Status', 'status')),
)
EXPENSES = CatalogResource(
    'expense', 'expenses', 'expense', 'expenses', Expense,
    (('date', 'Date', 'text'), ('category_name', 'Category', 'text'), ('warehouse_name', 'Warehouse', 'text'),
     ('payment_account_name', 'Payment account', 'text'), ('amount', 'Amount', 'price'),
     ('paid_amount', 'Paid', 'price'), ('outstanding_amount', 'Outstanding', 'price'),
     ('payment_status', 'Payment status', 'text'), ('is_completed', 'Completed', 'boolean')),
)
ACCOUNTS = CatalogResource(
    'financial-account', 'financial-accounts', 'financial account', 'financial accounts', FinancialAccount,
    (('name', 'Name', 'text'), ('type_label', 'Type', 'text'), ('warehouse_name', 'Warehouse', 'text'),
     ('balance', 'Balance', 'price'), ('is_active', 'Status', 'status')),
)
TRANSFERS = CatalogResource(
    'account-transfer', 'account-transfers', 'account transfer', 'account transfers', AccountTransfer,
    (('transfer_number', 'Transfer', 'text'), ('date', 'Date', 'text'), ('source_account_name', 'From account', 'text'),
     ('destination_account_name', 'To account', 'text'), ('amount', 'Amount', 'price'),
     ('reference', 'Reference', 'text')),
)
STATEMENT = CatalogResource(
    'account-statement', 'account-statement', 'entry', 'statement entries', AccountTransaction,
    (('date', 'Date', 'text'), ('source', 'Source', 'text'), ('reference', 'Reference', 'text'),
     ('direction_label', 'Direction', 'text'), ('amount', 'Amount', 'price'), ('balance_after', 'Balance after', 'price')),
)
CASH_FLOW = CatalogResource(
    'cash-flow', 'cash-flow', 'cash flow entry', 'cash flow entries', AccountTransaction,
    (('date', 'Date', 'text'), ('account_name', 'Account', 'text'), ('source', 'Source', 'text'),
     ('reference', 'Reference', 'text'), ('direction_label', 'Direction', 'text'), ('amount', 'Amount', 'price')),
)
SALES_PAYMENTS = CatalogResource(
    'sales-payment', 'sales-payments', 'payment', 'sales payments', SalesPayment,
    (('name', 'Payment', 'text'), ('type_label', 'Type', 'text'), ('date', 'Date', 'text'), ('account_name', 'Account', 'text'),
     ('method_label', 'Method', 'text'), ('reference', 'Reference', 'text'), ('amount', 'Amount', 'price')),
)


class AccountingPageView(ProtectedAccountPageView):
    resource = None
    mode = 'list'

    def can(self, action):
        user = self.request.user
        permission = self.resource.permission(action)
        if self.resource is not CATEGORIES:
            return has_role_permissions(user, (permission,))
        return user.has_perm(permission)

    def get_permission_required(self):
        action = {'add': 'add', 'edit': 'change'}.get(self.mode)
        permissions = [self.resource.permission('view')]
        if action:
            permissions.append(self.resource.permission(action))
        return permissions

    def has_permission(self):
        if self.resource is not CATEGORIES:
            return has_role_permissions(self.request.user, self.get_permission_required()) and has_warehouse_scope(self.request.user)
        return super().has_permission()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        key = self.resource.key
        context.update(resource=self.resource, page_mode=self.mode, list_url=reverse(f'accounting_pages:{key}-list'))
        if self.resource is ACCOUNTS:
            context.update(page_section='accounting', finance_key='accounts', section_label='Accounting')
        else:
            context.update(page_section='expense', expense_key=key, section_label='Expenses')
        return context


def table_columns(resource):
    return [{'key': key, 'label': label, 'type': kind} for key, label, kind in resource.columns]


class AccountingListPageView(AccountingPageView):
    template_name = 'inventory/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        resource = self.resource
        can_change, can_delete = self.can('change'), self.can('delete')
        context.update(
            page_label=resource.plural.capitalize(), list_api_url=reverse(f'accounting:{resource.key}-list'),
            add_url=reverse(f'accounting_pages:add-{resource.key}'), can_add=self.can('add'), can_delete=can_delete,
            list_description={
                EXPENSES.key: 'Expenses by category and warehouse.',
                ACCOUNTS.key: 'Balances for cash, bank and mobile banking accounts.',
                CATEGORIES.key: 'Categories used to organize expenses.',
            }[resource.key],
            columns=table_columns(resource), hide_list_hint=resource is ACCOUNTS,
        )
        if resource is EXPENSES:
            context['view_url_template'] = reverse('accounting_pages:expense-detail', args=[0])
        elif resource is ACCOUNTS and has_role_permissions(self.request.user, STATEMENT_VIEW):
            context['view_url_template'] = reverse('accounting_pages:account-statement', args=[0])
        if can_change or can_delete or 'view_url_template' in context:
            context['columns'].append({
                'key': 'actions', 'label': 'Actions', 'type': 'edit',
                'view_label': 'Statement' if resource is ACCOUNTS else None,
            })
        if can_change:
            context['edit_url_template'] = reverse(f'accounting_pages:edit-{resource.key}', args=[0])
        if can_delete:
            context['delete_url_template'] = reverse(f'accounting:{resource.key}-detail', args=[0])
        if resource is EXPENSES:
            scope = warehouse_scope(self.request.user)
            context.update(
                filter_categories=ExpenseCategory.objects.all(),
                filter_warehouses=scoped_queryset(WareHouse.objects.order_by('name', 'pk'), scope, 'pk'),
                edit_disabled_field='is_completed', delete_disabled_field='has_payment_history',
            )
            if can_change:
                context.update(complete_api_url=reverse('accounting:expense-complete'), complete_id_field='expense_id',
                               complete_result_key='expense', complete_label='expense')
        return context


class ExpenseDetailPageView(AccountingPageView):
    resource = EXPENSES
    mode = 'detail'
    template_name = 'accounting/expense_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = warehouse_scope(self.request.user)
        expenses = scoped_queryset(Expense.objects.only('is_completed'), scope)
        expense = get_object_or_404(expenses, pk=self.kwargs['pk'])
        context.update(
            expense_id=expense.pk, page_label='Expense details',
            detail_api_url=reverse('accounting:expense-detail', args=[expense.pk]),
        )
        if self.can('change') and not expense.is_completed:
            context['edit_url'] = reverse('accounting_pages:edit-expense', args=[expense.pk])
        return context


class AccountingFormPageView(AccountingPageView):
    template_name = 'inventory/add.html'
    mode = 'add'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        resource = self.resource
        editing = self.mode == 'edit'
        heading = f'Edit {resource.singular}' if editing else f'Add {resource.singular}'
        context.update(
            page_label=heading, form_heading=heading, save_label='Save changes' if editing else heading,
            fields_template=f'accounting/fields/{resource.key}.html', hide_catalog_hint=True,
            create_api_url=reverse(f'accounting:{resource.key}-list'),
        )
        if resource is EXPENSES:
            scope = warehouse_scope(self.request.user)
            restricted = not all_warehouse_access(scope)
            context.update(
                amount_label=f'Amount ({settings.INVENTORY_BASE_CURRENCY})',
                assigned_warehouse=scope.warehouse_id if restricted else '',
                warehouse_locked=restricted, options_api_url=reverse('accounting:expense-options'),
            )
        if resource is ACCOUNTS:
            scope = warehouse_scope(self.request.user)
            context.update(
                account_types=FinancialAccount.Type.choices, shared_accounts=all_warehouse_access(scope),
                warehouses=scoped_queryset(WareHouse.objects.filter(is_active=True).order_by('name', 'pk'), scope, 'pk'),
                currency=settings.INVENTORY_BASE_CURRENCY,
            )
        if editing:
            queryset = resource.model.objects.all()
            if resource is not CATEGORIES:
                queryset = scoped_queryset(queryset, scope)
            record = get_object_or_404(queryset, pk=self.kwargs['pk'])
            endpoint = reverse(f'accounting:{resource.key}-detail', args=[record.pk])
            context.update(form_method='PATCH', create_api_url=endpoint, detail_api_url=endpoint)
            if resource is EXPENSES:
                context['options_api_url'] = reverse('accounting:expense-edit-options', args=[record.pk])
                context['readonly_field'] = 'is_completed'
                context['readonly_message'] = 'This expense has been completed and is read-only.'
            if resource is ACCOUNTS:
                context['financial_account'] = record
        return context


class FinancePageView(ProtectedAccountPageView):
    resource = None
    finance_key = None

    def can(self, *permissions):
        return has_role_permissions(self.request.user, permissions)

    def has_permission(self):
        return self.can(*self.get_permission_required()) and has_warehouse_scope(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['resource'] = self.resource
        if self.resource is SALES_PAYMENTS:
            context.update(page_section='sale', section_label='Sales')
        else:
            context.update(page_section='accounting', finance_key=self.finance_key, section_label='Accounting')
        return context

    def accounts(self):
        scope = warehouse_scope(self.request.user)
        return scoped_queryset(FinancialAccount.objects.all(), scope)

    def sales_order(self):
        orders = completed_orders(warehouse_scope(self.request.user)).select_related('warehouse')
        return get_object_or_404(orders, pk=self.kwargs['sales_order_pk'])


class FinanceListPageView(FinancePageView):
    template_name = 'accounting/finance_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_mode='list', page_label=self.resource.plural.capitalize(),
                       columns=table_columns(self.resource), hide_list_hint=True)
        return context


class AccountStatementPageView(FinanceListPageView):
    resource = STATEMENT
    permission_required = STATEMENT_VIEW
    finance_key = 'accounts'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account = get_object_or_404(accounts_with_balances(self.accounts()), pk=self.kwargs['pk'])
        context.update(
            page_label=f'{account.name} statement', list_heading=f'{account.name} statement', financial_account=account,
            list_api_url=reverse('accounting:account-statement', args=[account.pk]),
            secondary_url=reverse('accounting_pages:financial-account-list'), secondary_label='Back to accounts',
            list_description='Entries are shown in posting order, newest first.',
        )
        return context


class CashFlowListPageView(FinanceListPageView):
    resource = CASH_FLOW
    permission_required = STATEMENT_VIEW
    finance_key = 'cash-flow'
    template_name = 'accounting/cash_flow.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            page_label='Cash flow', list_heading='Cash flow', filter_accounts=self.accounts().only('name'),
            list_api_url=reverse('accounting:cash-flow-list'),
            list_description='Posted inflows and outflows in posting order, newest first.',
        )
        return context


class AccountTransferListPageView(FinanceListPageView):
    resource = TRANSFERS
    permission_required = TRANSFER_VIEW
    finance_key = 'transfers'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            list_api_url=reverse('accounting:account-transfer-list'), can_add=self.can('accounting.add_accounttransfer'),
            add_url=reverse('accounting_pages:add-account-transfer'), add_label='New transfer',
            list_description='Funds moved between financial accounts.',
        )
        return context


class SalesPaymentListPageView(FinanceListPageView):
    resource = SALES_PAYMENTS
    permission_required = SALES_PAYMENT_VIEW

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.sales_order()
        total, paid, refunded = sales_payment_totals(order)
        context.update(
            sales_order=order, payment_total=total, paid=paid, refunded=refunded, due=total - paid,
            currency=settings.INVENTORY_BASE_CURRENCY,
            list_api_url=reverse('accounting:sales-payment-list', args=[order.pk]),
            secondary_url=reverse('sale_pages:sale-detail', args=[order.pk]), secondary_label='Back to sale',
            list_description='Search by account or payment reference.',
        )
        return context


class FinancePostingPageView(FinancePageView):
    template_name = 'inventory/add.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            hide_catalog_hint=True, request_key=uuid4(), currency=settings.INVENTORY_BASE_CURRENCY,
            fields_template=f'accounting/fields/{self.resource.key}.html',
        )
        return context


class AccountTransferFormPageView(FinancePostingPageView):
    resource = TRANSFERS
    finance_key = 'transfers'
    permission_required = (*TRANSFER_VIEW, 'accounting.add_accounttransfer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            fields_accounts=self.accounts().filter(is_active=True),
            form_heading='New account transfer', save_label='Post transfer',
            form_description='Choose two accounts to move funds between them.',
            create_api_url=reverse('accounting:account-transfer-list'),
            list_url=reverse('accounting_pages:account-transfer-list'),
        )
        return context
