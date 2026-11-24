from collections import defaultdict
from decimal import ROUND_HALF_UP, localcontext

from django.db import connections, transaction
from django.db.models import DecimalField, F, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from sale.models import MONEY_PRECISION
from warehouse.scope import lock_warehouse_scope, scoped_queryset

from .models import (
    MAX_AMOUNT, ZERO, AccountTransaction, AccountTransfer, FinancialAccount, PurchasePayment,
    Income, SalesPayment, TransferType, base_currency,
)


ACCOUNT_VIEW = 'accounting.view_financialaccount'
EXPENSE_VIEW = 'accounting.view_expense'
STATEMENT_VIEW = (ACCOUNT_VIEW, 'accounting.view_accounttransaction')
TRANSFER_VIEW = (ACCOUNT_VIEW, 'accounting.view_accounttransfer')
PAYMENT_VIEW = (EXPENSE_VIEW, ACCOUNT_VIEW, 'accounting.view_expensepayment')
SALES_PAYMENT_VIEW = ('sale.view_salesorder', ACCOUNT_VIEW, 'accounting.view_salespayment')
PURCHASE_PAYMENT_VIEW = ('purchase.view_purchaseorder', ACCOUNT_VIEW, 'accounting.view_purchasepayment')
INCOME_PAYMENT_VIEW = ('accounting.view_income', ACCOUNT_VIEW, 'accounting.view_incomepayment')
EXPENSE_COMPLETE = ('accounting.change_expense',)


class ExpenseCompleted(APIException):
    status_code = 409
    default_detail = 'Completed expenses are read-only and cannot be changed or deleted.'
    default_code = 'expense_completed'


class IncomeCompleted(ExpenseCompleted):
    default_detail = 'Completed income is read-only and cannot be changed or deleted.'
    default_code = 'income_completed'


def accounts_with_balances(queryset):
    balances = AccountTransaction.objects.filter(account_id=OuterRef('pk')).values('balance_after')[:1]
    return queryset.annotate(balance=Coalesce(Subquery(balances), F('opening_balance'), output_field=DecimalField(
        max_digits=15, decimal_places=4,
    )))


def transfer_queryset(queryset, scope):
    return scoped_queryset(scoped_queryset(queryset, scope, 'source_account__warehouse_id'),
                           scope, 'destination_account__warehouse_id')


def paid_amount(record):
    income = isinstance(record, Income)
    if hasattr(record, 'ledger_payments'):
        postings = ((entry.direction, entry.amount) for payment in record.ledger_payments for entry in payment.ledger_rows)
    else:
        source = 'income_payment__income' if income else 'expense_payment__expense'
        postings = AccountTransaction.objects.filter(**{source: record}).order_by().values_list(
            'direction', 'amount',
        ).iterator()
    paid_direction = AccountTransaction.Direction.INFLOW if income else AccountTransaction.Direction.OUTFLOW
    return sum((amount if direction == paid_direction else -amount for direction, amount in postings), ZERO)


def sales_payment_totals(sales_order):
    with localcontext() as context:
        context.prec = 50
        total = (sales_order.grand_total * sales_order.exchange_rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
        postings = AccountTransaction.objects.filter(sales_payment__sales_order=sales_order)
        paid = refunded = ZERO
        for direction, amount in postings.order_by().values_list('direction', 'amount').iterator():
            if direction == AccountTransaction.Direction.INFLOW:
                paid += amount
            else:
                refunded += amount
        return total, paid, refunded


def return_refund_amount(order, quantities, previous):
    # Round cumulative values so partial returns reconcile to the original merchandise totals.
    before = after = ZERO
    for line in order.items.filter(pk__in=quantities.keys() | previous.keys()):
        returned = previous.get(line.pk, 0)
        line_total = line.line_total
        before += line_total * returned / line.quantity
        after += line_total * (returned + quantities.get(line.pk, 0)) / line.quantity
    rate = order.exchange_rate
    return ((after * rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
            - (before * rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP))


def lock_rows(queryset):
    # SQLite needs to acquire its write lock before reading balances or payment totals.
    if not connections[queryset.db].features.has_select_for_update:
        queryset.update(updated_at=F('updated_at'))
    return queryset.select_for_update().order_by('pk')


def lock_income_expense(model, pk, scope):
    return get_object_or_404(lock_rows(scoped_queryset(model.objects.filter(pk=pk), scope)))


def lock_financial_scope(actor, *permissions):
    scope = lock_warehouse_scope(actor)
    if not has_role_permissions(actor, permissions):
        raise PermissionDenied('You do not have permission to perform this accounting action.')
    return scope


def locked_accounts(scope, **account_ids):
    accounts = scoped_queryset(FinancialAccount.objects.filter(pk__in=account_ids.values()), scope)
    found = {account.pk: account for account in lock_rows(accounts)}
    if any(pk not in found for pk in account_ids.values()):
        raise ValidationError({field: 'Choose an account within your warehouse access.'
                               for field, pk in account_ids.items() if pk not in found})
    return {field: found[pk] for field, pk in account_ids.items()}


def check_active_account(account, field):
    if not account.is_active:
        raise ValidationError({field: 'Choose an active account.'})


def available_payment_accounts(scope):
    accounts = FinancialAccount.objects.filter(is_active=True, currency=base_currency())
    return scoped_queryset(accounts, scope)


def payment_account_options(accounts):
    return [{'value': account.pk, 'label': account.name, 'warehouse': account.warehouse_id} for account in accounts]


def payment_account_error(account, warehouse_id):
    if not account.is_active:
        return 'Choose an active payment account.'
    if account.warehouse_id is not None and account.warehouse_id != warehouse_id:
        return 'Choose a payment account for this warehouse or a shared account.'
    if account.currency != base_currency():
        return 'Choose a payment account in the base currency.'
    return None


def lock_payment_account(scope, account_id, warehouse_id):
    if account_id is None:
        raise ValidationError({'payment_account': 'Choose a payment account.'})
    account = locked_accounts(scope, payment_account=account_id)['payment_account']
    error = payment_account_error(account, warehouse_id)
    if error:
        raise ValidationError({'payment_account': error})
    return account

def audit_fields(actor):
    username = actor.get_username()
    return {'create_by': username, 'modified_by': username}


def post_transaction(account, direction, actor, *, date, amount, transfer=None, expense_payment=None, income_payment=None,
                     sales_payment=None, purchase_payment=None):
    if sales_payment:
        expected_direction = (AccountTransaction.Direction.OUTFLOW if sales_payment.sales_return_id
                              else AccountTransaction.Direction.INFLOW)
        if direction != expected_direction:
            raise ValidationError({'direction': 'The transaction direction must match its sale payment or refund.'})
    if purchase_payment:
        expected_direction = (AccountTransaction.Direction.INFLOW if purchase_payment.purchase_return_id
                              else AccountTransaction.Direction.OUTFLOW)
        if direction != expected_direction:
            raise ValidationError({'direction': 'The transaction direction must match its purchase payment or refund.'})
    last_balance = account.transactions.values_list('balance_after', flat=True).first()
    balance = account.opening_balance if last_balance is None else last_balance
    balance += amount if direction == AccountTransaction.Direction.INFLOW else -amount
    if balance < ZERO:
        raise ValidationError({'amount': 'The account has insufficient funds.'})
    if balance > MAX_AMOUNT:
        raise ValidationError({'amount': 'The resulting account balance is too large.'})
    return AccountTransaction.objects.create(
        account=account, direction=direction, date=date, amount=amount, balance_after=balance,
        transfer=transfer, expense_payment=expense_payment, income_payment=income_payment, sales_payment=sales_payment,
        purchase_payment=purchase_payment, **audit_fields(actor),
    )


def create_source(model, actor, request_key, **payload):
    record, created = model.objects.get_or_create(request_key=request_key, defaults={**payload, **audit_fields(actor)})
    if not created and any(getattr(record, field) != value for field, value in payload.items()):
        raise ValidationError({'request_key': 'This request key has already been used for different details.'})
    return record, created


@transaction.atomic
def post_transfer(actor, *, source_account, destination_account, amount, date, reference, request_key,
                  transfer_type=TransferType.FUND_TRANSFER):
    scope = lock_financial_scope(actor, *TRANSFER_VIEW, 'accounting.add_accounttransfer')
    if source_account == destination_account:
        raise ValidationError({'destination_account': 'Choose a different destination account.'})
    accounts = locked_accounts(scope, source_account=source_account, destination_account=destination_account)
    source, destination = accounts['source_account'], accounts['destination_account']
    record, created = create_source(AccountTransfer, actor, request_key, source_account_id=source.pk,
                                    destination_account_id=destination.pk, amount=amount, date=date, reference=reference,
                                    transfer_type=transfer_type)
    if created:
        for field, account in accounts.items():
            check_active_account(account, field)
        if source.currency != destination.currency:
            raise ValidationError({'destination_account': 'Both accounts must use the same currency.'})
        post_transaction(source, AccountTransaction.Direction.OUTFLOW, actor, date=date, amount=amount, transfer=record)
        post_transaction(destination, AccountTransaction.Direction.INFLOW, actor, date=date, amount=amount, transfer=record)
    return record, created


@transaction.atomic
def complete_income_expense(actor, model, pk):
    key = model._meta.model_name
    scope = lock_financial_scope(actor, f'accounting.change_{key}')
    record = lock_income_expense(model, pk, scope)
    if record.is_completed:
        return record, False
    account = lock_payment_account(scope, record.payment_account_id, record.warehouse_id)
    now = timezone.now()
    due = record.amount - paid_amount(record)
    if due > ZERO:
        date = timezone.localdate(now)
        payment = record.payments.create(
            account=account, amount=due, date=date, reference=f'{key.capitalize()} #{record.pk}',
            method=account.type, **audit_fields(actor),
        )
        direction = AccountTransaction.Direction.INFLOW if model is Income else AccountTransaction.Direction.OUTFLOW
        post_transaction(account, direction, actor, date=date, amount=due, **{f'{key}_payment': payment})
    record.is_completed = True
    record.completed_at = now
    record.modified_by = actor.get_username()
    record.save(update_fields=['is_completed', 'completed_at', 'modified_by', 'updated_at'])
    return record, True


def post_completed_sale_payment(sales_order, scope, actor, *, date):
    """Called while the sale completion transaction holds the order lock."""
    account = lock_payment_account(scope, sales_order.payment_account_id, sales_order.warehouse_id)
    total, paid, _ = sales_payment_totals(sales_order)
    due = total - paid
    if due <= ZERO:
        return
    if due > MAX_AMOUNT:
        raise ValidationError({'amount': 'The outstanding sale amount exceeds the ledger limit.'})
    payment = SalesPayment.objects.create(
        sales_order=sales_order, account=account, amount=due, date=date, reference=sales_order.order_number,
        method=account.type, **audit_fields(actor),
    )
    post_transaction(account, AccountTransaction.Direction.INFLOW, actor, date=date, amount=due,
                     sales_payment=payment)


def post_sales_return_refund(record, scope, actor, *, date, amount):
    """Refund the original collection accounts while the sale order is locked."""
    if amount <= ZERO:
        return
    refundable = defaultdict(lambda: ZERO)
    entries = AccountTransaction.objects.filter(sales_payment__sales_order=record.sales_order_id)
    for account_id, direction, posted in entries.order_by().values_list('account_id', 'direction', 'amount').iterator():
        refundable[account_id] += posted if direction == AccountTransaction.Direction.INFLOW else -posted

    # Lock accounts in primary-key order, matching internal transfers.
    for account_id, collected in sorted(refundable.items()):
        refund = min(amount, collected)
        if refund <= ZERO:
            continue
        if refund > MAX_AMOUNT:
            raise ValidationError({'amount': 'The sales refund exceeds the ledger limit.'})
        account = lock_payment_account(scope, account_id, record.sales_order.warehouse_id)
        payment = SalesPayment.objects.create(
            sales_order_id=record.sales_order_id, sales_return=record, account=account, amount=refund,
            date=date, reference=record.return_number, method=account.type, **audit_fields(actor),
        )
        post_transaction(account, AccountTransaction.Direction.OUTFLOW, actor, date=date, amount=refund,
                         sales_payment=payment)
        amount -= refund
        if amount == ZERO:
            break


def post_received_purchase_payment(purchase_order, scope, actor, *, date):
    """Called inside the transaction that receives and locks the purchase."""
    account = lock_payment_account(scope, purchase_order.payment_account_id, purchase_order.warehouse_id)
    with localcontext() as context:
        context.prec = 50
        amount = (purchase_order.grand_total * purchase_order.exchange_rate).quantize(
            MONEY_PRECISION, rounding=ROUND_HALF_UP,
        )
    if amount == ZERO:
        return
    if amount > MAX_AMOUNT:
        raise ValidationError({'amount': 'The purchase payment exceeds the ledger limit.'})
    payment = PurchasePayment.objects.create(
        purchase_order=purchase_order, account=account, amount=amount, date=date,
        reference=purchase_order.order_number, method=account.type, **audit_fields(actor),
    )
    post_transaction(account, AccountTransaction.Direction.OUTFLOW, actor, date=date, amount=amount,
                     purchase_payment=payment)


def post_purchase_return_refund(record, scope, actor, *, date, amount):
    """Credit the original paying account while the purchase order is locked."""
    if amount <= ZERO:
        return
    entries = AccountTransaction.objects.filter(purchase_payment__purchase_order_id=record.purchase_order_id)
    original = entries.filter(direction=AccountTransaction.Direction.OUTFLOW,
                              purchase_payment__purchase_return__isnull=True).first()
    if original is None:
        return
    refunds = entries.filter(direction=AccountTransaction.Direction.INFLOW).order_by().values_list('amount', flat=True)
    refunded = sum(refunds.iterator(), ZERO)
    refund = min(amount, original.amount - refunded)
    if refund <= ZERO:
        return
    account = lock_payment_account(scope, original.account_id, record.purchase_order.warehouse_id)
    payment = PurchasePayment.objects.create(
        purchase_order_id=record.purchase_order_id, purchase_return=record, account=account, amount=refund,
        date=date, reference=record.return_number, method=account.type, **audit_fields(actor),
    )
    post_transaction(account, AccountTransaction.Direction.INFLOW, actor, date=date, amount=refund,
                     purchase_payment=payment)
