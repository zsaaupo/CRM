from collections import defaultdict
from decimal import ROUND_HALF_UP, localcontext

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from common.models import SettlementMode
from purchase.models import PurchaseOrder, PurchaseReturnItem
from sale.models import MONEY_PRECISION, SalesOrder, SalesReturnItem
from warehouse.scope import scoped_queryset

from .models import AccountTransaction, PurchasePayment, SalesPayment, SettlementCommand, ZERO
from .services import (
    PURCHASE_PAYMENT_VIEW, SALES_PAYMENT_VIEW, audit_fields, create_source, lock_financial_scope,
    lock_payment_account, lock_rows, post_transaction,
)


def payment_sources(order):
    if isinstance(order, SalesOrder):
        return SalesPayment, 'sales_order', 'sales_return', AccountTransaction.Direction.INFLOW
    return PurchasePayment, 'purchase_order', 'purchase_return', AccountTransaction.Direction.OUTFLOW


def with_settlement_history(orders):
    sale = orders.model is SalesOrder
    returns = (SalesReturnItem.objects.filter(sales_return__is_completed=True) if sale
               else PurchaseReturnItem.objects.filter(purchase_return__is_completed=True))
    payments = SalesPayment if sale else PurchasePayment
    return orders.prefetch_related(
        Prefetch('items__return_items', queryset=returns, to_attr='posted_returns'),
        Prefetch('payments', queryset=payments.objects.filter(transaction__isnull=False).select_related('transaction'),
                 to_attr='applied_payments'),
    )


def balance(order):
    model, _, _, paid_direction = payment_sources(order)
    is_sale = model is SalesPayment
    if not hasattr(order, 'applied_payments'):
        order = with_settlement_history(type(order).objects.filter(pk=order.pk)).get()
    posted = order.is_completed if is_sale else order.is_received
    paid = refunded = ZERO
    for payment in order.applied_payments:
        if payment.transaction.direction == paid_direction:
            paid += payment.transaction.amount
        else:
            refunded += payment.transaction.amount
    with localcontext() as context:
        context.prec = 50
        total = (order.grand_total * order.exchange_rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP) if posted else ZERO
        returned_value = ZERO
        for line in order.items.all():
            quantity = sum(row.quantity for row in line.posted_returns)
            if quantity:
                returned_value += line.line_total * quantity / line.quantity
        returned = (returned_value * order.exchange_rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
        due = total - returned - paid + refunded
    status = 'pending' if not posted else 'paid' if due <= ZERO else 'partial' if paid > refunded else 'due'
    return dict(total=total, paid=paid, refunded=refunded, returned=returned, due=due, payment_status=status)


@transaction.atomic
def settle_order(actor, order_model, pk, *, request_key, date, reference, tenders):
    is_sale = order_model is SalesOrder
    permissions = SALES_PAYMENT_VIEW if is_sale else PURCHASE_PAYMENT_VIEW
    permission = 'accounting.add_salespayment' if is_sale else 'accounting.add_purchasepayment'
    scope = lock_financial_scope(actor, *permissions, permission)
    order = get_object_or_404(lock_rows(scoped_queryset(order_model.objects.filter(pk=pk), scope)))
    if not (order.is_completed if is_sale else order.is_received):
        raise ValidationError('Post the order before recording payment.')
    model, order_field, _, direction = payment_sources(order)
    details = dict(order_type=order_field, order=pk, date=date.isoformat(), reference=reference,
                   tenders=[{**line, 'amount': format(line['amount'], '.4f')} for line in tenders])
    command, created = create_source(SettlementCommand, actor, request_key, details=details)
    if created:
        if sum(line['amount'] for line in tenders) > balance(order)['due']:
            raise ValidationError({'tenders': 'Payments exceed the open balance.'})
        accounts = {key: lock_payment_account(scope, key, order.warehouse_id)
                    for key in sorted({line['account'] for line in tenders})}
        for line in tenders:
            account = accounts[line['account']]
            payment = model.objects.create(command=command, account=account, method=line['method'], date=date,
                                           amount=line['amount'], reference=reference, **{order_field: order},
                                           **audit_fields(actor))
            source = 'sales_payment' if is_sale else 'purchase_payment'
            post_transaction(account, direction, actor, date=date, amount=line['amount'], **{source: payment})
    return command, balance(order), created


def refund_credit(record, order, scope, actor, *, date, amount):
    # Completion already claimed this return under the order lock. Its credit is included in due.
    remaining = amount
    if order.settlement_mode == SettlementMode.DEFERRED:
        remaining = min(amount, -balance(order)['due'])
    if remaining <= ZERO:
        return
    model, order_field, return_field, paid_direction = payment_sources(order)
    refundable = defaultdict(lambda: ZERO)
    for account_id, method, direction, posted in order.payments.filter(transaction__isnull=False).values_list(
        'account_id', 'method', 'transaction__direction', 'transaction__amount',
    ):
        refundable[account_id, method] += posted if direction == paid_direction else -posted
    is_sale = model is SalesPayment
    direction = AccountTransaction.Direction.OUTFLOW if is_sale else AccountTransaction.Direction.INFLOW
    for (account_id, method), available in sorted(refundable.items()):
        amount = min(remaining, available)
        if amount <= ZERO:
            continue
        account = lock_payment_account(scope, account_id, order.warehouse_id)
        payment = model.objects.create(account=account, amount=amount, method=method, date=date,
                                       reference=record.return_number, **{order_field: order, return_field: record},
                                       **audit_fields(actor))
        source = 'sales_payment' if is_sale else 'purchase_payment'
        post_transaction(account, direction, actor, date=date, amount=amount, **{source: payment})
        remaining -= amount
        if remaining == ZERO:
            return
