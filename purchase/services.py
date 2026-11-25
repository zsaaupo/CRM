"""Receive a complete purchase once and protect received purchase writes."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from accounting.services import post_received_purchase_payment
from common.models import SettlementMode
from inventory.models import Product
from warehouse.movements import record_lines
from warehouse.models import Stock, WareHouse
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from account.permissions import has_role_permissions
from .models import PurchaseOrder


MAX_STOCK_QUANTITY = Decimal('999999999999.999999')


class PurchaseReceived(APIException):
    status_code = 409
    default_detail = 'Received purchases and their items are read-only and cannot be changed or deleted.'
    default_code = 'purchase_received'


class PurchaseBusy(APIException):
    status_code = 409
    default_detail = 'This purchase or stock is being updated. Retry the request; receiving the same purchase does not add stock twice.'
    default_code = 'purchase_busy'


def lock_editable_purchase(pk, actor):
    """Call inside atomic. A conditional write also serializes SQLite writers."""
    try:
        scope = lock_warehouse_scope(actor)
        orders = scoped_queryset(PurchaseOrder.objects.all(), scope)
        orders.filter(pk=pk, is_received=False).update(is_received=False)
        order = orders.select_for_update().get(pk=pk)
    except PurchaseOrder.DoesNotExist as exc:
        raise NotFound('Purchase order not found.') from exc
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise PurchaseBusy() from exc
        raise
    if order.is_received:
        raise PurchaseReceived()
    return order


class PurchaseDeleteGuardMixin:
    @transaction.atomic
    def perform_destroy(self, instance):
        if isinstance(instance, PurchaseOrder):
            instance = lock_editable_purchase(instance.pk, self.request.user)
            if instance.items.exists():
                if not has_role_permissions(self.request.user, ('purchase.delete_purchaseorderitem',)):
                    raise PermissionDenied('You do not have permission to delete purchase order items.')
                instance.items.all().delete()
        else:
            lock_editable_purchase(instance.purchase_order_id, self.request.user)
            current = type(instance).objects.filter(pk=instance.pk).first()
            if current is None:
                raise NotFound('Purchase order item not found.')
            if current.purchase_order_id != instance.purchase_order_id:
                raise PurchaseBusy()
            instance = current
        return super().perform_destroy(instance)


def receive_purchase(actor, purchase_id):
    if not has_role_permissions(actor, ('purchase.change_purchaseorder',)):
        raise PermissionDenied('You do not have permission to receive a purchase.')
    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            if not has_role_permissions(actor, ('purchase.change_purchaseorder',)):
                raise PermissionDenied('Your role does not have permission to receive a purchase.')
            orders = scoped_queryset(PurchaseOrder.objects.all(), scope)
            now = timezone.now()
            # This is both a once-only claim and the first database write. Losing
            # requests wait/retry and then see True; any later failure rolls it back.
            claimed = orders.filter(pk=purchase_id, is_received=False).update(
                is_received=True, modified_by=actor.get_username(), updated_at=now)
            try:
                order = orders.select_for_update().get(pk=purchase_id)
            except PurchaseOrder.DoesNotExist as exc:
                raise NotFound('Purchase order not found.') from exc
            if not claimed:
                return order, False
            if not order.warehouse_id:
                raise ValidationError({'warehouse': 'Set the receiving warehouse on this purchase before receiving it.'})
            warehouse = WareHouse.objects.select_for_update().filter(pk=order.warehouse_id).first()
            if warehouse is None or not warehouse.is_active:
                raise ValidationError({'warehouse': 'Choose an active receiving warehouse.'})
            items = list(order.items.order_by('line_number', 'pk'))
            if not items:
                raise ValidationError({'items': 'Add at least one purchase item before receiving.'})
            products = {product.pk: product for product in Product.objects.select_for_update().filter(
                pk__in={item.product_id for item in items}).order_by('pk')}
            quantities = defaultdict(Decimal)
            for item in items:
                product = products.get(item.product_id)
                if product is None or item.unit_id != product.unit_id:
                    raise ValidationError({'items': f'Line {item.line_number}: the purchase unit must match the product unit.'})
                if item.quantity <= 0:
                    raise ValidationError({'items': 'Purchase quantities must be positive.'})
                quantities[item.product_id] += item.quantity
            for product_id, quantity in sorted(quantities.items()):
                if quantity > MAX_STOCK_QUANTITY:
                    raise ValidationError({'quantity': 'The received quantity exceeds the supported stock limit.'})
                stock, _ = Stock.objects.get_or_create(warehouse=warehouse, product_id=product_id,
                    defaults={'quantity': Decimal('0'), 'create_by': actor.get_username(),
                              'modified_by': actor.get_username()})
                expected_quantity = stock.quantity + quantity
                updated = Stock.objects.filter(pk=stock.pk, quantity__lte=MAX_STOCK_QUANTITY - quantity).update(
                    quantity=F('quantity') + quantity, updated_at=now, modified_by=actor.get_username())
                if not updated:
                    raise ValidationError({'quantity': 'Receiving this purchase would exceed the stock quantity limit.'})
                stock.refresh_from_db(fields=['quantity'])
                if stock.quantity != expected_quantity:
                    raise ValidationError({'quantity': 'The database cannot store this resulting stock quantity accurately.'})
            record_lines(warehouse.pk, order, items, 'receipt', order.order_date, actor)
            if order.settlement_mode == SettlementMode.AUTO_FULL:
                post_received_purchase_payment(order, scope, actor, date=timezone.localdate(now))
            return order, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise PurchaseBusy() from exc
        raise
    except IntegrityError as exc:
        raise PurchaseBusy('The purchase could not be received. Check the stock references and retry.') from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The stock quantity exceeds the database precision. The purchase was not received.'}) from exc
