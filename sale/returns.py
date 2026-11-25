"""Validate sales returns and post stock and refund changes atomically."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.services import post_sales_return_refund, return_refund_amount
from audit_log.service import record_activity
from inventory.models import Product
from warehouse.models import Stock, WareHouse
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from warehouse.services import MAX_STOCK_QUANTITY

from .models import SalesOrder, SalesReturn, SalesReturnItem


SOURCE_PERMISSIONS = ('sale.view_salesorder', 'sale.view_salesorderitem')
RETURN_VIEW = ('sale.view_salesreturn',)
RETURN_WRITE = (*SOURCE_PERMISSIONS, 'sale.add_salesreturn', 'sale.add_salesreturnitem')
RETURN_CREATE = (*RETURN_VIEW, *RETURN_WRITE)
RETURN_EDIT = (*RETURN_VIEW, *SOURCE_PERMISSIONS, 'sale.change_salesreturn', 'sale.view_salesreturnitem',
               'sale.add_salesreturnitem', 'sale.change_salesreturnitem', 'sale.delete_salesreturnitem')


class ReturnCompleted(APIException):
    status_code = 409
    default_detail = 'Completed sales returns are read-only.'
    default_code = 'return_completed'


class ReturnBusy(APIException):
    status_code = 409
    default_detail = 'This return or its stock is being updated. Retry the request. A return can restock only once.'
    default_code = 'return_busy'


def require_permissions(user, *permissions):
    if not has_role_permissions(user, permissions):
        raise PermissionDenied('Your role does not have permission for this sales return action.')


def completed_orders(scope):
    return scoped_queryset(SalesOrder.objects.filter(is_completed=True, warehouse__isnull=False), scope)


def scoped_returns(queryset, scope):
    return scoped_queryset(queryset, scope, 'sales_order__warehouse_id')


def lock_return(pk, user, action='change'):
    scope = lock_warehouse_scope(user)
    require_permissions(user, f'sale.{action}_salesreturn')
    records = scoped_returns(SalesReturn.objects.all(), scope)
    records.filter(pk=pk, is_completed=False).update(is_completed=False)
    record = records.select_for_update().filter(pk=pk).first()
    if record is None:
        raise NotFound('Sales return not found.')
    if record.is_completed:
        raise ReturnCompleted()
    return record


def returned_quantities(order, exclude_return=None):
    returned = defaultdict(Decimal)
    lines = SalesReturnItem.objects.filter(sales_return__sales_order=order, sales_return__is_completed=True)
    if exclude_return is not None:
        lines = lines.exclude(sales_return_id=exclude_return)
    for pk, quantity in lines.values_list('sales_order_item_id', 'quantity'):
        returned[pk] += quantity
    return returned


def validate_items(order, quantities, products=None, *, previous=None):
    if not quantities:
        raise ValidationError({'items': 'Add at least one return quantity.'})
    sold = {line.pk: line for line in order.items.select_related('product').filter(pk__in=quantities)}
    returned = returned_quantities(order) if previous is None else previous
    for pk, quantity in quantities.items():
        line = sold.get(pk)
        if line is None:
            raise ValidationError({'items': 'Choose items from the original sales order.'})
        product = line.product if products is None else products[line.product_id]
        if not product.is_returnable:
            raise ValidationError({'items': f'Line {line.line_number}: {product.name} is marked as non-returnable.'})
        if product.unit_id != line.unit_id:
            raise ValidationError({'items': f'Line {line.line_number}: the original sale unit must match the product unit.'})
        remaining = line.quantity - returned[pk]
        if not quantity.is_finite() or quantity <= 0 or quantity > remaining:
            raise ValidationError({'items': f'Line {line.line_number}: enter a positive quantity up to {max(remaining, 0)}.'})
    return sold


def restock(warehouse, quantities, user, now):
    username = user.get_username()
    for product_id, quantity in sorted(quantities.items()):
        if quantity > MAX_STOCK_QUANTITY:
            raise ValidationError({'quantity': 'The combined returned quantity exceeds the stock limit.'})
        stock, _ = Stock.objects.select_for_update().get_or_create(warehouse=warehouse, product_id=product_id,
            defaults={'quantity': Decimal('0'), 'create_by': username, 'modified_by': username})
        expected = stock.quantity + quantity
        updated = Stock.objects.filter(pk=stock.pk, quantity__lte=MAX_STOCK_QUANTITY - quantity).update(
            quantity=F('quantity') + quantity, updated_at=now, modified_by=username)
        if not updated:
            raise ValidationError({'quantity': 'Completing this return would exceed the warehouse stock limit.'})
        stock.refresh_from_db(fields=['quantity'])
        if stock.quantity != expected:
            raise ValidationError({'quantity': 'The database cannot store the resulting stock quantity accurately.'})
        record_activity(stock, 'stock_changed', actor=user)


def complete_return(user, pk):
    require_permissions(user, 'sale.change_salesreturn')
    try:
        with transaction.atomic(), localcontext() as context:
            context.prec = 50
            scope = lock_warehouse_scope(user)
            require_permissions(user, 'sale.change_salesreturn')
            records = scoped_returns(SalesReturn.objects.all(), scope)
            now = timezone.now()
            claimed = records.filter(pk=pk, is_completed=False).update(is_completed=True, completed_at=now,
                modified_by=user.get_username(), updated_at=now)
            record = records.select_for_update().filter(pk=pk).first()
            if record is None:
                raise NotFound('Sales return not found.')
            if not claimed:
                return record, False

            # Serialize competing returns against the same completed sale before totaling returned quantities.
            orders = completed_orders(scope).filter(pk=record.sales_order_id)
            if not orders.update(is_completed=F('is_completed')):
                raise ValidationError({'sales_order': 'A completed sale with a warehouse is required.'})
            order = orders.get()
            record.sales_order = order
            warehouse = WareHouse.objects.select_for_update().filter(pk=order.warehouse_id, is_active=True).first()
            if warehouse is None:
                raise ValidationError({'warehouse': 'The original sale warehouse must be active.'})
            if record.return_date < order.order_date:
                raise ValidationError({'return_date': 'The return date cannot be before the sales order date.'})
            quantities = dict(record.items.values_list('sales_order_item_id', 'quantity'))
            product_ids = order.items.filter(pk__in=quantities).values_list('product_id', flat=True)
            products = {product.pk: product for product in Product.objects.select_for_update().filter(
                pk__in=product_ids).order_by('pk')}
            previous = returned_quantities(order, exclude_return=pk)
            sold = validate_items(order, quantities, products=products, previous=previous)
            stock_quantities = defaultdict(Decimal)
            for line_id, quantity in quantities.items():
                stock_quantities[sold[line_id].product_id] += quantity
            restock(warehouse, stock_quantities, user, now)
            post_sales_return_refund(record, scope, user, date=timezone.localdate(now),
                                     amount=return_refund_amount(order, quantities, previous))
            record_activity(record, 'completed', actor=user)
            return record, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise ReturnBusy() from exc
        raise
    except IntegrityError as exc:
        raise ReturnBusy() from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The resulting quantity exceeds database precision.'}) from exc
