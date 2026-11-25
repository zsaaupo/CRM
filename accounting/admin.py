from django.contrib import admin
from django.core.exceptions import PermissionDenied

from account.permissions import has_role_permissions
from common.admin import AuditedAdmin
from warehouse.admin import WarehouseScopedAdmin
from warehouse.scope import scoped_queryset

from .models import (
    AccountTransaction, AccountTransfer, DepositEntry, Expense, ExpenseCategory, ExpensePayment, FinancialAccount,
    PartySettlement, PurchasePayment, Income, IncomeCategory, IncomePayment, SalesPayment,
)
from .services import (
    INCOME_PAYMENT_VIEW, PAYMENT_VIEW, PURCHASE_PAYMENT_VIEW, SALES_PAYMENT_VIEW, STATEMENT_VIEW, TRANSFER_VIEW, available_payment_accounts, lock_rows,
    transfer_queryset,
)


class PaymentAccountAdminMixin:
    scoped_foreign_keys = {'warehouse': 'pk', 'payment_account': 'warehouse_id'}

    def related_records(self, db_field, scope):
        if db_field.name == 'payment_account':
            return available_payment_accounts(scope)
        return super().related_records(db_field, scope)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'payment_account':
            kwargs['required'] = True
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class LockedAccountingAdmin(WarehouseScopedAdmin):
    def check_existing_records(self, scope, ids):
        records = self.scope_queryset(self.model.objects.filter(pk__in=ids), scope)
        if {obj.pk for obj in lock_rows(records) if self.is_unposted(obj)} != set(ids):
            raise PermissionDenied('A record is outside your warehouse access or has posted activity.')


@admin.register(ExpenseCategory, IncomeCategory)
class ExpenseCategoryAdmin(AuditedAdmin):
    list_display = ['name', 'is_active']
    search_fields = ['name']
    list_filter = ['is_active']


@admin.register(Expense, Income)
class ExpenseAdmin(PaymentAccountAdminMixin, LockedAccountingAdmin):
    list_display = ['date', 'category', 'warehouse', 'amount', 'is_completed', 'create_by']
    list_filter = ['date', ('category', admin.RelatedOnlyFieldListFilter),
                   ('warehouse', admin.RelatedOnlyFieldListFilter)]
    list_select_related = ['category', 'warehouse']
    search_fields = ['category__name', 'warehouse__name']

    def is_unposted(self, obj):
        return obj is None or (not obj.is_completed and not obj.payments.exists())


@admin.register(FinancialAccount)
class FinancialAccountAdmin(LockedAccountingAdmin):
    list_display = ['name', 'type', 'warehouse', 'currency', 'opening_balance', 'is_active']
    list_filter = ['type', 'is_active', ('warehouse', admin.RelatedOnlyFieldListFilter)]
    list_select_related = ['warehouse']
    search_fields = ['name', 'warehouse__name']

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return [*fields, 'type', 'warehouse', 'opening_balance'] if obj else fields


class PostedFinanceAdmin(WarehouseScopedAdmin):
    actions = None
    view_permissions = ()
    scoped_foreign_keys = {}

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and has_role_permissions(request.user, self.view_permissions)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccountTransaction)
class AccountTransactionAdmin(PostedFinanceAdmin):
    warehouse_lookup = 'account__warehouse_id'
    view_permissions = STATEMENT_VIEW
    list_display = ['date', 'account', 'direction', 'amount', 'balance_after', 'transfer', 'expense_payment',
                    'income_payment', 'sales_payment', 'purchase_payment']
    list_select_related = ['account']
    list_filter = ['direction', 'date']
    # exclude = ['transfer', 'expense_payment', 'sales_payment']


@admin.register(AccountTransfer)
class AccountTransferAdmin(PostedFinanceAdmin):
    view_permissions = TRANSFER_VIEW
    list_display = ['number', 'date', 'source_account', 'destination_account', 'amount', 'reference']
    list_select_related = ['source_account', 'destination_account']
    search_fields = ['reference', 'source_account__name', 'destination_account__name']

    def scope_queryset(self, queryset, scope):
        return transfer_queryset(queryset, scope)


@admin.register(ExpensePayment)
class ExpensePaymentAdmin(PostedFinanceAdmin):
    view_permissions = PAYMENT_VIEW
    warehouse_lookup = 'expense__warehouse_id'
    list_display = ['number', 'date', 'expense', 'account', 'amount', 'method', 'reference']
    list_select_related = ['expense', 'expense__category', 'account']
    search_fields = ['reference', 'account__name']

    def scope_queryset(self, queryset, scope):
        return scoped_queryset(super().scope_queryset(queryset, scope), scope, 'account__warehouse_id')


@admin.register(IncomePayment)
class IncomePaymentAdmin(ExpensePaymentAdmin):
    view_permissions = INCOME_PAYMENT_VIEW
    warehouse_lookup = 'income__warehouse_id'
    list_display = ['number', 'date', 'income', 'account', 'amount', 'method', 'reference']
    list_select_related = ['income', 'income__category', 'account']


@admin.register(SalesPayment)
class SalesPaymentAdmin(PostedFinanceAdmin):
    view_permissions = SALES_PAYMENT_VIEW
    warehouse_lookup = 'sales_order__warehouse_id'
    list_display = ['number', 'date', 'sales_order', 'sales_return', 'account', 'amount', 'method', 'reference']
    list_select_related = ['sales_order', 'sales_return', 'account']
    search_fields = ['reference', 'account__name', 'sales_order__order_number']

    def scope_queryset(self, queryset, scope):
        return scoped_queryset(super().scope_queryset(queryset, scope), scope, 'account__warehouse_id')


@admin.register(PurchasePayment)
class PurchasePaymentAdmin(PostedFinanceAdmin):
    view_permissions = PURCHASE_PAYMENT_VIEW
    warehouse_lookup = 'purchase_order__warehouse_id'
    list_display = ['number', 'date', 'purchase_order', 'purchase_return', 'account', 'amount', 'method', 'reference']
    list_select_related = ['purchase_order', 'purchase_return', 'account']
    search_fields = ['reference', 'account__name', 'purchase_order__order_number']

    def scope_queryset(self, queryset, scope):
        return scoped_queryset(super().scope_queryset(queryset, scope), scope, 'account__warehouse_id')


@admin.register(PartySettlement)
class PartySettlementAdmin(PostedFinanceAdmin):
    view_permissions = ('accounting.clear_due',)
    warehouse_lookup = 'account__warehouse_id'
    list_display = ['pk', 'party', 'kind', 'total', 'method', 'payment_date']
    list_select_related = ['party', 'account']
    search_fields = ['party__name', 'note']


@admin.register(DepositEntry)
class DepositEntryAdmin(PostedFinanceAdmin):
    view_permissions = ('accounting.add_deposit',)
    warehouse_lookup = 'account__warehouse_id'
    list_display = ['customer', 'kind', 'amount', 'source', 'account', 'sales_payment', 'request_key']
    list_select_related = ['customer', 'account', 'sales_payment']
