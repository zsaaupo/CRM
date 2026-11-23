"""Validate supplier returns and post stock and refund changes atomically."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.services import post_purchase_return_refund, return_refund_amount
from inventory.models import Product
from warehouse.models import Stock, WareHouse
from warehouse.scope import lock_warehouse_scope, scoped_queryset

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
    return scoped_queryset(PurchaseOrder.objects.filter(is_received=True, warehouse__isnull=False), scope)


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
        if product.unit_id != line.unit_id:
            raise ValidationError({'items': f'Line {line.line_number}: the original purchase unit must match the product unit.'})
        remaining = line.quantity - returned[pk]
        if not quantity.is_finite() or quantity <= 0 or quantity > remaining:
            raise ValidationError({'items': f'Line {line.line_number}: enter a positive quantity up to {max(remaining, 0)}.'})
    return received


def deduct_stock(warehouse, quantities, products, user, now):
    username = user.get_username()
    for product_id, quantity in sorted(quantities.items()):
        stock = Stock.objects.select_for_update().filter(warehouse=warehouse, product_id=product_id).first()
        updated = stock is not None and Stock.objects.filter(pk=stock.pk, quantity__gte=quantity).update(
            quantity=F('quantity') - quantity, updated_at=now, modified_by=username)
        if not updated:
            raise ValidationError({'quantity': f'Insufficient stock for {products[product_id].name} in the receiving warehouse.'})
        expected = stock.quantity - quantity
        stock.refresh_from_db(fields=['quantity'])
        if stock.quantity != expected:
            raise ValidationError({'quantity': 'The database cannot store the resulting stock quantity accurately.'})


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
            stock_quantities = defaultdict(Decimal)
            for line_id, quantity in quantities.items():
                stock_quantities[received[line_id].product_id] += quantity
            deduct_stock(warehouse, stock_quantities, products, user, now)
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
