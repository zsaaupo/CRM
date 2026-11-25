"""Validate sales returns and post stock and refund changes atomically."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.services import post_sales_return_refund, return_refund_amount
from common.money import q_qty
from inventory.models import Product, require_convertible_unit
from warehouse.models import StockMovement, StockSerial, WareHouse
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from warehouse.services import MAX_STOCK_QUANTITY, post_stock_movement

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
        require_convertible_unit(line.unit, product.unit, field='items')
        remaining = line.quantity - returned[pk]
        if not quantity.is_finite() or quantity <= 0 or quantity > remaining:
            raise ValidationError({'items': f'Line {line.line_number}: enter a positive quantity up to {max(remaining, 0)}.'})
    return sold


def restock(warehouse, quantities, user, now):
    pass


def _restock_return_lines(record, order, warehouse, quantities, sold, user):
    for line_id in sorted(quantities, key=lambda lid: (sold[lid].product_id, lid)):
        qty = quantities[line_id]
        sale_line = sold[line_id]
        product = sale_line.product
        factor = getattr(sale_line, 'unit_conversion_factor', Decimal('1')) or Decimal('1')
        base_qty = q_qty(qty * factor)
        if product.product_type_id in ('service', 'digital'):
            continue
        if product.product_type_id == 'combo':
            for comp in product.combo_items.select_related('component_product').order_by('component_product_id'):
                if comp.component_product.product_type_id in ('service', 'digital'):
                    continue
                comp_qty = q_qty(base_qty * comp.quantity)
                post_stock_movement(
                    warehouse_id=warehouse.pk,
                    product_id=comp.component_product_id,
                    kind=StockMovement.Kind.SALE_RETURN,
                    quantity_delta=comp_qty,
                    unit_cost_base=None,
                    movement_date=record.return_date,
                    reference_type='sales_return',
                    reference_id=record.pk,
                    reference_line_id=line_id,
                    posting_key=f'sales_return:{record.pk}:line:{line_id}:comp:{comp.component_product_id}',
                    actor=user,
                )
            continue
        serial_ids = []
        if product.has_imei_serial and sale_line.serial_numbers:
            serial_ids = list(
                StockSerial.objects.filter(
                    product_id=product.pk, serial_number__in=sale_line.serial_numbers[:int(base_qty)],
                ).values_list('pk', flat=True)
            )
        post_stock_movement(
            warehouse_id=warehouse.pk,
            product_id=sale_line.product_id,
            kind=StockMovement.Kind.SALE_RETURN,
            quantity_delta=base_qty,
            unit_cost_base=sale_line.unit_cost_base,
            batch_id=sale_line.batch_id,
            serial_ids=serial_ids,
            serial_status=StockSerial.Status.RETURNED,
            movement_date=record.return_date,
            reference_type='sales_return',
            reference_id=record.pk,
            reference_line_id=line_id,
            posting_key=f'sales_return:{record.pk}:line:{line_id}',
            actor=user,
        )


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
            _restock_return_lines(record, order, warehouse, quantities, sold, user)
            post_sales_return_refund(record, scope, user, date=timezone.localdate(now),
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
