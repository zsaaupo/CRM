from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from audit_log.service import record_activity
from inventory.models import Product
from .models import Stock, StockAdjustment, StockAdjustmentItem, WareHouse
from .scope import lock_warehouse_scope, scoped_queryset
from .services import MAX_STOCK_QUANTITY
from .stock_counts import validate_count_adjustment


class AdjustmentCompleted(APIException):
    status_code = 409
    default_detail = 'Completed adjustments are read-only.'
    default_code = 'adjustment_completed'


class AdjustmentBusy(APIException):
    status_code = 409
    default_detail = 'This adjustment or its stock is being updated. Please retry.'
    default_code = 'adjustment_busy'


def lock_editable_adjustment(adjustment_id, scope):
    """Call within the draft write transaction, after locking warehouse scope."""
    records = scoped_queryset(StockAdjustment.objects.filter(pk=adjustment_id), scope)
    # Make an initial write to serialize competing draft writes and completion on SQLite too.
    records.filter(is_completed=False).update(is_completed=False)
    record = records.select_for_update().first()
    if record is None:
        raise NotFound('Stock adjustment not found.')
    if record.is_completed:
        raise AdjustmentCompleted()
    return record


def adjustment_changes(items):
    product_ids = {item.product_id for item in items}
    products = {product.pk: product for product in Product.objects.select_for_update().filter(
        pk__in=product_ids,
    ).order_by('pk')}
    changes = defaultdict(Decimal)
    for item in items:
        product = products[item.product_id]
        if item.unit_id != product.unit_id:
            raise ValidationError({'items': f'The unit for {product.sku} changed. Save the draft with the current product unit.'})
        if not item.quantity.is_finite() or item.quantity <= 0:
            raise ValidationError({'items': f'Enter a positive quantity for {product.sku}.'})
        changes[product.pk] += item.quantity if item.operation == StockAdjustmentItem.Operation.ADD else -item.quantity
    return products, changes


def apply_stock_change(warehouse, product, change, username, now):
    stock = Stock.objects.select_for_update().filter(warehouse=warehouse, product=product).first()
    if change < 0 and (stock is None or stock.quantity < -change):
        raise ValidationError({'quantity': f'Insufficient stock for {product.sku} in {warehouse.name}.'})
    expected = (stock.quantity if stock else Decimal('0')) + change
    if expected > MAX_STOCK_QUANTITY:
        raise ValidationError({'quantity': f'The resulting stock for {product.sku} exceeds the quantity limit.'})
    if stock is None:
        stock = Stock.objects.create(warehouse=warehouse, product=product, quantity=expected,
                                     create_by=username, modified_by=username)
    else:
        Stock.objects.filter(pk=stock.pk).update(quantity=expected, updated_at=now, modified_by=username)
        record_activity(stock, 'stock_changed', actor=username)
    stock.refresh_from_db(fields=['quantity'])
    if stock.quantity != expected:
        raise ValidationError({'quantity': f'The database cannot store the resulting stock for {product.sku} accurately.'})


def complete_adjustment(actor, adjustment_id):
    permission = ('warehouse.change_stockadjustment',)
    if not has_role_permissions(actor, permission):
        raise PermissionDenied('Your role does not have permission to complete stock adjustments.')
    try:
        with transaction.atomic(), localcontext() as context:
            context.prec = 50
            scope = lock_warehouse_scope(actor)
            if not has_role_permissions(actor, permission):
                raise PermissionDenied('Your role does not have permission to complete stock adjustments.')
            records = scoped_queryset(StockAdjustment.objects.filter(pk=adjustment_id), scope)
            now = timezone.now()
            username = actor.get_username()
            claimed = records.filter(is_completed=False).update(
                is_completed=True, completed_at=now, modified_by=username, updated_at=now,
            )
            record = records.select_for_update().first()
            if record is None:
                raise NotFound('Stock adjustment not found.')
            if not claimed:
                return record, False
            warehouse = WareHouse.objects.select_for_update().filter(pk=record.warehouse_id, is_active=True).first()
            if warehouse is None:
                raise ValidationError({'warehouse': 'Choose an active warehouse.'})
            items = list(record.items.all())
            if not items:
                raise ValidationError({'items': 'Add at least one product before completing the adjustment.'})
            products, changes = adjustment_changes(items)
            validate_count_adjustment(record, changes)
            for product_id, change in sorted(changes.items()):
                if change:
                    apply_stock_change(warehouse, products[product_id], change, username, now)
            record_activity(record, 'completed', actor=actor)
            return record, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise AdjustmentBusy() from exc
        raise
    except IntegrityError as exc:
        raise AdjustmentBusy() from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The resulting stock exceeds the database precision.'}) from exc
