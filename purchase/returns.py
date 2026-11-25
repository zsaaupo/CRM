"""Validate supplier returns and post stock and refund changes atomically."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.services import post_purchase_return_refund, return_refund_amount
from common.money import q_cost, q_qty
from inventory.models import Product, require_convertible_unit
from warehouse.models import StockMovement, WareHouse
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from warehouse.services import post_stock_movement

from .models import PurchaseOrder, PurchaseReturn, PurchaseReturnItem


SOURCE_PERMISSIONS = ('purchase.view_purchaseorder', 'purchase.view_purchaseorderitem')
RETURN_VIEW = ('purchase.view_purchasereturn',)
RETURN_WRITE = (*SOURCE_PERMISSIONS, 'purchase.add_purchasereturn', 'purchase.add_purchasereturnitem')
RETURN_CREATE = (*RETURN_VIEW, *RETURN_WRITE)
RETURN_EDIT = (*RETURN_VIEW, *SOURCE_PERMISSIONS, 'purchase.change_purchasereturn', 'purchase.view_purchasereturnitem',
               'purchase.add_purchasereturnitem', 'purchase.change_purchasereturnitem', 'purchase.delete_purchasereturnitem')


class ReturnCompleted(APIException):
    status_code = 409
    default_detail = 'Completed purchase returns are read-only.'
    default_code = 'return_completed'


class ReturnBusy(APIException):
    status_code = 409
    default_detail = 'This return or its stock is being updated. Retry the request. A return can deduct stock only once.'
    default_code = 'return_busy'


def require_permissions(user, *permissions):
    if not has_role_permissions(user, permissions):
        raise PermissionDenied('Your role does not have permission for this purchase return action.')


def received_orders(scope):
    return scoped_queryset(
        PurchaseOrder.objects.filter(
            Q(is_received=True) | Q(receipt_state=PurchaseOrder.ReceiptState.PARTIAL),
            warehouse__isnull=False,
        ),
        scope,
    )


def scoped_returns(queryset, scope):
    return scoped_queryset(queryset, scope, 'purchase_order__warehouse_id')


def lock_return(pk, user, action='change'):
    scope = lock_warehouse_scope(user)
    require_permissions(user, f'purchase.{action}_purchasereturn')
    records = scoped_returns(PurchaseReturn.objects.all(), scope)
    records.filter(pk=pk, is_completed=False).update(is_completed=False)
    record = records.select_for_update().filter(pk=pk).first()
    if record is None:
        raise NotFound('Purchase return not found.')
    if record.is_completed:
        raise ReturnCompleted()
    return record


def returned_quantities(order, exclude_return=None):
    returned = defaultdict(Decimal)
    lines = PurchaseReturnItem.objects.filter(purchase_return__purchase_order=order, purchase_return__is_completed=True)
    if exclude_return is not None:
        lines = lines.exclude(purchase_return_id=exclude_return)
    for pk, quantity in lines.values_list('purchase_order_item_id', 'quantity'):
        returned[pk] += quantity
    return returned


def validate_items(order, quantities, products=None, *, previous=None):
    if not quantities:
        raise ValidationError({'items': 'Add at least one return quantity.'})
    received = {line.pk: line for line in order.items.select_related('product').filter(pk__in=quantities)}
    returned = returned_quantities(order) if previous is None else previous
    for pk, quantity in quantities.items():
        line = received.get(pk)
        if line is None:
            raise ValidationError({'items': 'Choose items from the original purchase order.'})
        product = line.product if products is None else products[line.product_id]
        if not product.is_returnable:
            raise ValidationError({'items': f'Line {line.line_number}: {product.name} is marked as non-returnable.'})
        require_convertible_unit(line.unit, product.unit, field='items')
        received_cap = (
            line.received_quantity
            if (order.receipt_state == PurchaseOrder.ReceiptState.PARTIAL or line.received_quantity > 0)
            else line.quantity
        )
        remaining = received_cap - returned[pk]
        if not quantity.is_finite() or quantity <= 0 or quantity > remaining:
            raise ValidationError({'items': f'Line {line.line_number}: enter a positive quantity up to {max(remaining, 0)}.'})
    return received


def deduct_stock(warehouse, quantities, products, user, now):
    pass


def _deduct_return_lines(record, order, warehouse, quantities, received, user):
    for line_id in sorted(quantities, key=lambda lid: (received[lid].product_id, lid)):
        qty = quantities[line_id]
        po_line = received[line_id]
        product = po_line.product
        if product.product_type_id in ('service', 'digital'):
            continue
        factor = getattr(po_line, 'unit_conversion_factor', Decimal('1')) or Decimal('1')
        base_qty = q_qty(qty * factor)
        out_cost = po_line.unit_cost_base
        if out_cost is None and po_line.quantity:
            out_cost = q_cost((po_line.net_amount / (po_line.quantity * factor)) * order.exchange_rate)
        post_stock_movement(
            warehouse_id=warehouse.pk,
            product_id=po_line.product_id,
            kind=StockMovement.Kind.PURCHASE_RETURN,
            quantity_delta=-base_qty,
            unit_cost_base=out_cost,
            batch_id=po_line.batch_id,
            movement_date=record.return_date,
            reference_type='purchase_return',
            reference_id=record.pk,
            reference_line_id=line_id,
            posting_key=f'purchase_return:{record.pk}:line:{line_id}',
            actor=user,
        )


def complete_return(user, pk):
    require_permissions(user, 'purchase.change_purchasereturn')
    try:
        with transaction.atomic(), localcontext() as context:
            context.prec = 50
            scope = lock_warehouse_scope(user)
            require_permissions(user, 'purchase.change_purchasereturn')
            records = scoped_returns(PurchaseReturn.objects.all(), scope)
            now = timezone.now()
            claimed = records.filter(pk=pk, is_completed=False).update(is_completed=True, completed_at=now,
                modified_by=user.get_username(), updated_at=now)
            record = records.select_for_update().filter(pk=pk).first()
            if record is None:
                raise NotFound('Purchase return not found.')
            if not claimed:
                return record, False

            # Serialize competing returns against the same receipt before totaling returned quantities.
            orders = received_orders(scope).filter(pk=record.purchase_order_id)
            if not orders.update(is_received=F('is_received')):
                raise ValidationError({'purchase_order': 'A received purchase with a receiving warehouse is required.'})
            order = orders.get()
            record.purchase_order = order
            warehouse = WareHouse.objects.select_for_update().filter(pk=order.warehouse_id, is_active=True).first()
            if warehouse is None:
                raise ValidationError({'warehouse': 'The original purchase warehouse must be active.'})
            if record.return_date < order.order_date:
                raise ValidationError({'return_date': 'The return date cannot be before the purchase order date.'})
            quantities = dict(record.items.values_list('purchase_order_item_id', 'quantity'))
            product_ids = order.items.filter(pk__in=quantities).values_list('product_id', flat=True)
            products = {product.pk: product for product in Product.objects.select_for_update().filter(
                pk__in=product_ids).order_by('pk')}
            previous = returned_quantities(order, exclude_return=pk)
            received = validate_items(order, quantities, products=products, previous=previous)
            _deduct_return_lines(record, order, warehouse, quantities, received, user)
            post_purchase_return_refund(record, scope, user, date=timezone.localdate(now),
                                        amount=return_refund_amount(order, quantities, previous))
            return record, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise ReturnBusy() from exc
        raise
    except IntegrityError as exc:
        raise ReturnBusy() from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The resulting quantity exceeds database precision.'}) from exc
