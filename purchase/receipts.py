from decimal import Decimal, ROUND_HALF_UP, localcontext
from uuid import uuid5

from django.db import OperationalError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.models import AccountTransaction, PurchasePayment, SettlementCommand, MAX_AMOUNT
from accounting.services import audit_fields, create_source, lock_payment_account, lock_rows, post_transaction
from accounting.settlement import balance
from common.models import SettlementMode
from warehouse.models import StockMovement, WareHouse
from warehouse.movements import COST_PRECISION, post_valued_movements
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from .models import MONEY_PRECISION, PurchaseOrder, PurchaseReceipt, PurchaseReceiptLine
from .services import PurchaseBusy, receive_stock


def cumulative_value(order, items, *, complete):
    value = sum((line.line_total * line.received_quantity / line.quantity for line in items), Decimal('0'))
    if complete:
        value += order.shipping_cost
    return (value * order.exchange_rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


def record_receipt(actor, order, request_key, quantities):
    """The caller holds the purchase lock. Freeze each delivery's incremental value."""
    existing = PurchaseReceipt.objects.filter(request_key=request_key).first()
    if existing:
        if existing.order_id != order.pk or dict(existing.lines.values_list('order_item_id', 'quantity')) != quantities:
            raise ValidationError({'request_key': 'The key was already used for a different receipt.'})
        return existing, False
    if order.is_received:
        raise ValidationError({'order': 'This purchase has already been received in full.'})
    wh = get_object_or_404(WareHouse.objects.select_for_update(), pk=order.warehouse_id, is_active=True)
    items = list(order.items.order_by('pk'))
    selected = [line for line in items if line.pk in quantities]
    if not selected or len(selected) != len(quantities):
        raise ValidationError({'lines': 'Choose one or more lines from this order.'})
    now = timezone.now()
    with localcontext() as context:
        context.prec = 60
        before = cumulative_value(order, items, complete=False)
        for line in selected:
            amount = quantities[line.pk]
            if amount <= 0 or amount > line.quantity - line.received_quantity:
                raise ValidationError({'lines': f'Line {line.line_number} exceeds the outstanding quantity.'})
            line.received_quantity += amount
        complete = all(line.received_quantity == line.quantity for line in items)
        value = cumulative_value(order, items, complete=complete) - before
        receipt = PurchaseReceipt.objects.create(order=order, request_key=request_key, amount_base=value,
            final_delivery=complete, **audit_fields(actor))
        receive_stock(wh, selected, quantities, actor, now)
        movements = []
        for line in selected:
            line.save(update_fields=['received_quantity'])
            detail = PurchaseReceiptLine.objects.create(receipt=receipt, order_item=line, quantity=quantities[line.pk])
            movements.append(StockMovement(warehouse=wh, product_id=line.product_id, unit_id=line.unit_id,
                kind=StockMovement.Kind.RECEIPT, quantity=detail.quantity, source_id=order.pk, source_line_id=line.pk,
                posting_key=f'purchase-receipt/{receipt.pk}/{line.pk}', posted_by=actor.get_username(),
                business_date=timezone.localdate(now), unit_cost_base=(line.net_amount * order.exchange_rate /
                    line.quantity).quantize(COST_PRECISION, rounding=ROUND_HALF_UP)))
        post_valued_movements(wh.pk, movements)
    order.is_received = complete
    order.receipts_started = True
    order.modified_by = actor.get_username()
    order.save(update_fields=['receipts_started', 'is_received', 'modified_by', 'updated_at'])
    return receipt, True


def match_invoice(actor, scope, order, receipt_id, reference):
    receipt = get_object_or_404(order.receipts.select_for_update(), pk=receipt_id)
    if receipt.invoiced_at:
        if receipt.invoice_reference != reference:
            raise ValidationError({'invoice_reference': 'The receipt has a different matched invoice.'})
        return receipt, False
    now = timezone.now()
    receipt.invoiced_at = now
    receipt.invoiced_by = actor.get_username()
    receipt.invoice_reference = reference
    receipt.save(update_fields=['invoiced_at', 'invoiced_by', 'invoice_reference', 'updated_at'])
    for detail in receipt.lines.select_related('order_item'):
        line = detail.order_item
        line.invoiced_quantity += detail.quantity
        line.save(update_fields=['invoiced_quantity'])
    if order.settlement_mode == SettlementMode.AUTO_FULL:
        amount = balance(order)['due']
        if amount > MAX_AMOUNT:
            raise ValidationError({'amount': 'The payment exceeds the ledger limit.'})
        if amount > 0:
            account = lock_payment_account(scope, order.payment_account_id, order.warehouse_id)
            command, _ = create_source(SettlementCommand, actor, uuid5(receipt.request_key, 'automatic-invoice-payment'),
                                        details={'receipt': receipt.pk, 'invoice': reference})
            payment = PurchasePayment.objects.create(command=command, account=account, purchase_order=order,
                amount=amount, reference=reference, method=account.type, date=timezone.localdate(now), **audit_fields(actor))
            post_transaction(account, AccountTransaction.Direction.OUTFLOW, actor, date=payment.date,
                             amount=amount, purchase_payment=payment)
    return receipt, True


def post_receipt_action(actor, pk, data, *, invoice=False):
    permission = 'purchase.match_purchaseinvoice' if invoice else 'purchase.change_purchaseorder'
    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            if not has_role_permissions(actor, ('purchase.view_purchaseorder', 'purchase.view_purchaseorderitem', permission)):
                raise PermissionDenied()
            order = get_object_or_404(lock_rows(scoped_queryset(PurchaseOrder.objects.filter(pk=pk), scope)))
            if invoice:
                return match_invoice(actor, scope, order, data['receipt'], data['invoice_reference'])
            return record_receipt(actor, order, data['request_key'], {r['line']: r['quantity'] for r in data['lines']})
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise PurchaseBusy() from exc
        raise
