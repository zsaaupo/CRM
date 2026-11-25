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

from .models import (
    AccountTransaction, AccountTransfer, Expense, ExpenseCategory, FinancialAccount, Income, IncomeCategory,
    PurchasePayment, SalesPayment, TransferType,
)
from .services import (
    PURCHASE_PAYMENT_VIEW, SALES_PAYMENT_VIEW, STATEMENT_VIEW, TRANSFER_VIEW, accounts_with_balances,
    compute_order_balance, sales_payment_totals,
)


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
INCOME_CATEGORIES = CatalogResource(
    'income-category', 'income-categories', 'income category', 'income categories', IncomeCategory, CATEGORIES.columns,
)
INCOMES = CatalogResource(
    'income', 'incomes', 'income', 'income records', Income, EXPENSES.columns,
)
ACCOUNTS = CatalogResource(
    'financial-account', 'financial-accounts', 'financial account', 'financial accounts', FinancialAccount,
    (('name', 'Name', 'text'), ('type_label', 'Type', 'text'), ('warehouse_name', 'Warehouse', 'text'),
     ('balance', 'Balance', 'price'), ('is_active', 'Status', 'status')),
)
TRANSFERS = CatalogResource(
    'account-transfer', 'account-transfers', 'fund transfer', 'fund transfers', AccountTransfer,
    (('transfer_number', 'Transfer', 'text'), ('date', 'Date', 'text'), ('source_account_name', 'From account', 'text'),
     ('destination_account_name', 'To account', 'text'), ('amount', 'Amount', 'price'),
     ('reference', 'Reference', 'text')),
)
MONEY_TRANSFERS = CatalogResource(
    'money-transfer', 'money-transfers', 'money transfer', 'money transfers', AccountTransfer, TRANSFERS.columns,
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
        if self.resource not in (CATEGORIES, INCOME_CATEGORIES):
            return has_role_permissions(user, (permission,))
        return user.has_perm(permission)

    def get_permission_required(self):
        action = {'add': 'add', 'edit': 'change'}.get(self.mode)
        permissions = [self.resource.permission('view')]
        if action:
            permissions.append(self.resource.permission(action))
        return permissions

    def has_permission(self):
        if self.resource not in (CATEGORIES, INCOME_CATEGORIES):
            return has_role_permissions(self.request.user, self.get_permission_required()) and has_warehouse_scope(self.request.user)
        return super().has_permission()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        key = self.resource.key
        context.update(resource=self.resource, page_mode=self.mode, list_url=reverse(f'accounting_pages:{key}-list'))
        if self.resource is ACCOUNTS:
            context.update(page_section='accounting', finance_key='accounts', section_label='Accounting')
        elif self.resource in (INCOMES, INCOME_CATEGORIES):
            context.update(page_section='income', income_key=key, section_label='Income')
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
                INCOMES.key: 'Income by category and warehouse.',
                INCOME_CATEGORIES.key: 'Categories used to organize income.',
                EXPENSES.key: 'Expenses by category and warehouse.',
                ACCOUNTS.key: 'Balances for cash, bank and mobile banking accounts.',
                CATEGORIES.key: 'Categories used to organize expenses.',
            }[resource.key],
            columns=table_columns(resource), hide_list_hint=resource is ACCOUNTS,
        )
        if resource in (EXPENSES, INCOMES):
            context['view_url_template'] = reverse(f'accounting_pages:{resource.key}-detail', args=[0])
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
        if resource in (EXPENSES, INCOMES):
            scope = warehouse_scope(self.request.user)
            context.update(
                filter_categories=resource.model._meta.get_field('category').related_model.objects.all(),
                filter_warehouses=scoped_queryset(WareHouse.objects.order_by('name', 'pk'), scope, 'pk'),
                edit_disabled_field='is_completed', delete_disabled_field='has_payment_history',
            )
            if can_change:
                context.update(complete_api_url=reverse(f'accounting:{resource.key}-complete'),
                               complete_id_field=f'{resource.key}_id', complete_result_key=resource.key,
                               complete_label=resource.singular)
        return context


class ExpenseDetailPageView(AccountingPageView):
    resource = EXPENSES
    mode = 'detail'
    template_name = 'accounting/expense_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = warehouse_scope(self.request.user)
        records = scoped_queryset(self.resource.model.objects.only('is_completed'), scope)
        record = get_object_or_404(records, pk=self.kwargs['pk'])
        key = self.resource.key
        context.update(
            record_id=record.pk, page_label=f'{key.capitalize()} details',
            detail_api_url=reverse(f'accounting:{key}-detail', args=[record.pk]),
        )
        if self.can('change') and not record.is_completed:
            context['edit_url'] = reverse(f'accounting_pages:edit-{key}', args=[record.pk])
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
        if resource in (EXPENSES, INCOMES):
            scope = warehouse_scope(self.request.user)
            restricted = not all_warehouse_access(scope)
            context.update(
                fields_template='accounting/fields/expense.html',
                account_label='Receive into account' if resource is INCOMES else 'Pay from account',
                amount_label=f'Amount ({settings.INVENTORY_BASE_CURRENCY})',
                assigned_warehouse=scope.warehouse_id if restricted else '',
                warehouse_locked=restricted, options_api_url=reverse(f'accounting:{resource.key}-options'),
            )
        if resource is INCOME_CATEGORIES:
            context['fields_template'] = 'accounting/fields/expense-category.html'
        if resource is ACCOUNTS:
            scope = warehouse_scope(self.request.user)
            context.update(
                account_types=FinancialAccount.Type.choices, shared_accounts=all_warehouse_access(scope),
                warehouses=scoped_queryset(WareHouse.objects.filter(is_active=True).order_by('name', 'pk'), scope, 'pk'),
                currency=settings.INVENTORY_BASE_CURRENCY,
            )
        if editing:
            queryset = resource.model.objects.all()
            if resource not in (CATEGORIES, INCOME_CATEGORIES):
                queryset = scoped_queryset(queryset, scope)
            record = get_object_or_404(queryset, pk=self.kwargs['pk'])
            endpoint = reverse(f'accounting:{resource.key}-detail', args=[record.pk])
            context.update(form_method='PATCH', create_api_url=endpoint, detail_api_url=endpoint)
            if resource in (EXPENSES, INCOMES):
                context['options_api_url'] = reverse(f'accounting:{resource.key}-edit-options', args=[record.pk])
                context['readonly_field'] = 'is_completed'
                context['readonly_message'] = f'This {resource.singular} has been completed and is read-only.'
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


class LedgerPageView(FinancePageView):
    template_name = 'accounting/ledger.html'
    permission_required = STATEMENT_VIEW
    finance_key = 'ledger'


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
    transfer_type = TransferType.FUND_TRANSFER

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            finance_key=self.resource.key,
            list_api_url=reverse('accounting:account-transfer-list', query={'transfer_type': self.transfer_type}),
            can_add=self.can('accounting.add_accounttransfer'),
            add_url=reverse(f'accounting_pages:add-{self.resource.key}'), add_label='New transfer',
            list_description='Funds moved between financial accounts.',
        )
        return context


class SalesPaymentListPageView(FinanceListPageView):
    resource = SALES_PAYMENTS
    permission_required = SALES_PAYMENT_VIEW

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.sales_order()
        bal = compute_order_balance(order)
        context.update(
            sales_order=order, payment_total=bal.net_billable, paid=bal.net_paid,
            refunded=bal.refunds_issued, due=bal.open_due, payment_status=bal.payment_status,
            currency=settings.INVENTORY_BASE_CURRENCY,
            list_api_url=reverse('accounting:sales-payment-list', args=[order.pk]),
            secondary_url=reverse('sale_pages:sale-detail', args=[order.pk]), secondary_label='Back to sale',
            list_description='Search by account or payment reference.',
        )
        return context


class PurchasePaymentListPageView(FinanceListPageView):
    resource = CatalogResource(
        'purchase-payment', 'purchase-payments', 'payment', 'purchase payments', PurchasePayment,
        (('name', 'Payment', 'text'), ('type_label', 'Type', 'text'), ('date', 'Date', 'text'),
         ('account_name', 'Account', 'text'), ('method_label', 'Method', 'text'),
         ('reference', 'Reference', 'text'), ('amount', 'Amount', 'price')),
    )
    permission_required = PURCHASE_PAYMENT_VIEW

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from purchase.returns import received_orders
        order = get_object_or_404(received_orders(warehouse_scope(self.request.user)).select_related('warehouse'),
                                  pk=self.kwargs['purchase_order_pk'])
        bal = compute_order_balance(order)
        context.update(
            page_section='purchase', section_label='Purchase',
            purchase_order=order, payment_total=bal.net_billable, paid=bal.net_paid,
            refunded=bal.refunds_issued, due=bal.open_due, payment_status=bal.payment_status,
            currency=settings.INVENTORY_BASE_CURRENCY,
            list_api_url=reverse('accounting:purchase-payment-list', args=[order.pk]),
            secondary_url=reverse('purchase_pages:purchase-detail', args=[order.pk]), secondary_label='Back to purchase',
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
    transfer_type = TransferType.FUND_TRANSFER
    permission_required = (*TRANSFER_VIEW, 'accounting.add_accounttransfer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            fields_accounts=self.accounts().filter(is_active=True),
            finance_key=self.resource.key, fields_template='accounting/fields/account-transfer.html',
            form_heading=f'New {self.resource.singular}', save_label='Post transfer',
            transfer_type=self.transfer_type,
            form_description='Choose two accounts to move funds between them.',
            create_api_url=reverse('accounting:account-transfer-list'),
            list_url=reverse(f'accounting_pages:{self.resource.key}-list'),
        )
        return context
