from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from inventory.models import Product
from warehouse.models import Stock, WareHouse
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from account.permissions import has_role_permissions
from .models import SalesOrder


class SaleCompleted(APIException):
    status_code = 409
    default_detail = 'Completed sales and their items are read-only and cannot be changed or deleted.'
    default_code = 'sale_completed'


class SaleBusy(APIException):
    status_code = 409
    default_detail = 'This sale or stock is being updated. Retry the request; completing the same sale does not deduct stock twice.'
    default_code = 'sale_busy'


def lock_order(pk, actor):
    """Call inside atomic. Serialize order/line edits with completion, including on SQLite."""
    try:
        scope = lock_warehouse_scope(actor)
        orders = scoped_queryset(SalesOrder.objects.all(), scope)
        orders.filter(pk=pk, is_completed=False).update(is_completed=False)
        order = orders.select_for_update().get(pk=pk)
    except SalesOrder.DoesNotExist as exc:
        raise NotFound('Sales order not found.') from exc
    if order.is_completed:
        raise SaleCompleted()
    return order


def require_item_permission(request, action):
    if request is not None and not has_role_permissions(request.user, (f'sale.{action}_salesorderitem',)):
        raise PermissionDenied(f'You do not have permission to {action} sales order items.')


@transaction.atomic
def delete_order(order, request):
    order = lock_order(order.pk, request.user)
    if not has_role_permissions(request.user, ('sale.delete_salesorder',)):
        raise PermissionDenied('Your role does not have permission to delete sales orders.')
    if order.items.exists():
        require_item_permission(request, 'delete')
        order.items.all().delete()
    order.delete()


def complete_sale(actor, sale_id):
    if not has_role_permissions(actor, ('sale.change_salesorder',)):
        raise PermissionDenied('You do not have permission to complete a sale.')
    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            if not has_role_permissions(actor, ('sale.change_salesorder',)):
                raise PermissionDenied('Your role does not have permission to complete a sale.')
            orders = scoped_queryset(SalesOrder.objects.all(), scope)
            now = timezone.now()
            # Claim once as the first write; errors roll back both the flag and stock.
            claimed = orders.filter(pk=sale_id, is_completed=False).update(
                is_completed=True, fulfilled_at=now, modified_by=actor.get_username(), updated_at=now)
            try:
                order = orders.select_for_update().get(pk=sale_id)
            except SalesOrder.DoesNotExist as exc:
                raise NotFound('Sales order not found.') from exc
            if not claimed:
                return order, False
            if not order.warehouse_id:
                raise ValidationError({'warehouse': 'Set the warehouse on this sale before completing it.'})
            warehouse = WareHouse.objects.select_for_update().filter(pk=order.warehouse_id).first()
            if warehouse is None or not warehouse.is_active:
                raise ValidationError({'warehouse': 'Choose an active warehouse.'})
            items = list(order.items.order_by('line_number', 'pk'))
            if not items:
                raise ValidationError({'items': 'Add at least one sale item before completing.'})
            products = {product.pk: product for product in Product.objects.select_for_update().filter(
                pk__in={item.product_id for item in items}).order_by('pk')}
            quantities = defaultdict(Decimal)
            for item in items:
                product = products.get(item.product_id)
                if product is None or item.unit_id != product.unit_id:
                    raise ValidationError({'items': f'Line {item.line_number}: the sale unit must match the product unit.'})
                if item.quantity <= 0:
                    raise ValidationError({'items': 'Sale quantities must be positive.'})
                quantities[item.product_id] += item.quantity
            for product_id, quantity in sorted(quantities.items()):
                stock = Stock.objects.select_for_update().filter(warehouse=warehouse, product_id=product_id).first()
                if stock is None or stock.quantity < quantity:
                    raise ValidationError({'quantity': f'Insufficient stock for {products[product_id].sku} in {warehouse.name}.'})
                expected_quantity = stock.quantity - quantity
                updated = Stock.objects.filter(pk=stock.pk, quantity__gte=quantity).update(
                    quantity=F('quantity') - quantity, updated_at=now, modified_by=actor.get_username())
                if not updated:
                    raise ValidationError({'quantity': f'Insufficient stock for {products[product_id].sku}.'})
                stock.refresh_from_db(fields=['quantity'])
                if stock.quantity != expected_quantity:
                    raise ValidationError({'quantity': 'The database cannot store this resulting stock quantity accurately.'})
            return order, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise SaleBusy() from exc
        raise
    except IntegrityError as exc:
        raise SaleBusy('The sale could not be completed. Check the stock references and retry.') from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The stock quantity exceeds the database precision. The sale was not completed.'}) from exc
