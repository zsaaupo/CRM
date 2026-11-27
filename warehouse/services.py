"""Atomic warehouse transfers, using the same stock balances as purchases and sales."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from audit_log.service import record_activity
from inventory.models import Product
from .models import Stock, WareHouse, WarehouseTransfer
from .scope import all_warehouse_access, check_warehouse, lock_warehouse_scope


MAX_STOCK_QUANTITY = Decimal('999999999999.999999')


def deduct_stock(warehouse, quantities, products, actor, now):
    for product_id, quantity in sorted(quantities.items()):
        stock = Stock.objects.select_for_update().filter(warehouse=warehouse, product_id=product_id).first()
        if stock is None or stock.quantity < quantity:
            raise ValidationError({'quantity': f'Insufficient stock for {products[product_id].sku} in {warehouse.name}.'})
        expected = stock.quantity - quantity
        updated = Stock.objects.filter(pk=stock.pk, quantity__gte=quantity).update(
            quantity=F('quantity') - quantity, updated_at=now, modified_by=actor.get_username())
        if not updated:
            raise ValidationError({'quantity': f'Insufficient stock for {products[product_id].sku}.'})
        stock.refresh_from_db(fields=['quantity'])
        if stock.quantity != expected:
            raise ValidationError({'quantity': 'The database cannot store this resulting stock quantity accurately.'})
        record_activity(stock, 'stock_changed', actor=actor)


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


class TransferCompleted(APIException):
    status_code = 409
    default_detail = 'Completed transfers are read-only and cannot be changed or deleted.'
    default_code = 'transfer_completed'


class TransferBusy(APIException):
    status_code = 409
    default_detail = 'This transfer or its stock is being updated. Retry the request; a transfer only moves stock once.'
    default_code = 'transfer_busy'


def transfer_queryset(queryset, scope, *, write=False):
    if all_warehouse_access(scope):
        return queryset
    source = Q(source_warehouse_id=scope.warehouse_id)
    destination = Q(destination_warehouse_id=scope.warehouse_id)
    return queryset.filter(source & destination if write else source | destination)


def require_transfer_permission(actor, action):
    if not has_role_permissions(actor, (f'warehouse.{action}_warehousetransfer',)):
        raise PermissionDenied(f'Your role does not have permission to {action} warehouse transfers.')


def check_transfer_warehouses(scope, source_id, destination_id):
    check_warehouse(scope, source_id)
    check_warehouse(scope, destination_id)


def lock_transfer(pk, actor, action='change'):
    """Call inside atomic; serialize edits/deletes with completion and scope changes."""
    scope = lock_warehouse_scope(actor)
    require_transfer_permission(actor, action)
    records = transfer_queryset(WarehouseTransfer.objects.all(), scope, write=True)
    records.filter(pk=pk, is_completed=False).update(is_completed=False)
    try:
        record = records.select_for_update().get(pk=pk)
    except WarehouseTransfer.DoesNotExist as exc:
        raise NotFound('Warehouse transfer not found.') from exc
    if record.is_completed:
        raise TransferCompleted()
    return record


def complete_transfer(actor, transfer_id):
    require_transfer_permission(actor, 'change')
    try:
        with transaction.atomic(), localcontext() as context:
            context.prec = 50
            scope = lock_warehouse_scope(actor)
            require_transfer_permission(actor, 'change')
            records = transfer_queryset(WarehouseTransfer.objects.prefetch_related('items__product', 'items__unit'), scope, write=True)
            now = timezone.now()
            claimed = records.filter(pk=transfer_id, is_completed=False).update(
                is_completed=True, modified_by=actor.get_username(), updated_at=now)
            try:
                record = records.select_for_update().get(pk=transfer_id)
            except WarehouseTransfer.DoesNotExist as exc:
                raise NotFound('Warehouse transfer not found.') from exc
            if not claimed:
                return record, False
            if record.source_warehouse_id == record.destination_warehouse_id:
                raise ValidationError({'destination_warehouse': 'Choose a different destination warehouse.'})
            warehouses = list(WareHouse.objects.select_for_update().filter(
                pk__in=[record.source_warehouse_id, record.destination_warehouse_id]).order_by('pk'))
            if len(warehouses) != 2 or any(not warehouse.is_active for warehouse in warehouses):
                raise ValidationError({'warehouse': 'Both warehouses must be active before completing a transfer.'})
            items = list(record.items.all())
            if not items:
                raise ValidationError({'items': 'Add at least one product before completing the transfer.'})
            # Stable lock order matches purchases/sales; repeated products share one balance update.
            products = {product.pk: product for product in Product.objects.select_for_update().filter(
                pk__in={item.product_id for item in items}).order_by('pk')}
            quantities = defaultdict(Decimal)
            for item in items:
                product = products.get(item.product_id)
                if product is None or item.unit_id != product.unit_id:
                    raise ValidationError({'items': f'Line {item.line_number}: use the product unit.'})
                if not item.quantity.is_finite() or item.quantity <= 0:
                    raise ValidationError({'items': 'Every transfer quantity must be positive.'})
                quantities[item.product_id] += item.quantity
            for product_id, quantity in sorted(quantities.items()):
                if quantity > MAX_STOCK_QUANTITY:
                    raise ValidationError({'quantity': 'The combined quantity exceeds the supported stock limit.'})
                balances = {stock.warehouse_id: stock for stock in Stock.objects.select_for_update().filter(
                    warehouse_id__in=[record.source_warehouse_id, record.destination_warehouse_id],
                    product_id=product_id).order_by('warehouse_id', 'pk')}
                source = balances.get(record.source_warehouse_id)
                if source is None or source.quantity < quantity:
                    raise ValidationError({'quantity': f'Insufficient stock for {products[product_id].sku} in the source warehouse.'})
                destination = balances.get(record.destination_warehouse_id)
                if destination is None:
                    destination = Stock.objects.create(warehouse_id=record.destination_warehouse_id,
                        product_id=product_id, quantity=Decimal('0'), create_by=actor.get_username(),
                        modified_by=actor.get_username())
                expected_source = source.quantity - quantity
                expected_destination = destination.quantity + quantity
                moved_out = Stock.objects.filter(pk=source.pk, quantity__gte=quantity).update(
                    quantity=F('quantity') - quantity, updated_at=now, modified_by=actor.get_username())
                moved_in = Stock.objects.filter(pk=destination.pk, quantity__lte=MAX_STOCK_QUANTITY - quantity).update(
                    quantity=F('quantity') + quantity, updated_at=now, modified_by=actor.get_username())
                if not moved_out:
                    raise ValidationError({'quantity': 'Insufficient stock in the source warehouse.'})
                if not moved_in:
                    raise ValidationError({'quantity': 'The destination stock would exceed the supported quantity limit.'})
                source.refresh_from_db(fields=['quantity'])
                destination.refresh_from_db(fields=['quantity'])
                if source.quantity != expected_source or destination.quantity != expected_destination:
                    raise ValidationError({'quantity': 'The database cannot store the resulting stock quantities accurately.'})
                record_activity(source, 'stock_changed', actor=actor)
                record_activity(destination, 'stock_changed', actor=actor)
            record_activity(record, 'completed', actor=actor)
            return record, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise TransferBusy() from exc
        raise
    except IntegrityError as exc:
        raise TransferBusy('The transfer could not be completed. Check its stock references and retry.') from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The resulting quantity exceeds database precision. Stock was not transferred.'}) from exc
