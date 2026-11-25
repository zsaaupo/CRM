"""Capture and reconcile physical stock counts within the warehouse scope."""
from collections import defaultdict
from decimal import Decimal, localcontext
from uuid import uuid4

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import CharField, F, OuterRef, Q, Subquery
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from audit_log.models import AuditLog
from audit_log.service import bulk_create_with_audit, record_activity
from inventory.models import Brand, ProductCategory
from .models import Stock, StockAdjustment, StockAdjustmentItem, StockCount, StockCountItem, WareHouse
from .scope import check_warehouse, lock_warehouse_scope, scoped_queryset


VIEW_COUNT = ('warehouse.view_stockcount',)
ADD_COUNT = (*VIEW_COUNT, 'warehouse.add_stockcount')
FINALIZE_COUNT = (*VIEW_COUNT, 'warehouse.change_stockcount')
POST_ADJUSTMENT = ('warehouse.add_stockadjustment', 'warehouse.change_stockadjustment')
DRAFT_COUNT_ADJUSTMENT = (*VIEW_COUNT, 'warehouse.add_stockadjustment')

COUNT_ORDERING = {
    'created_at': 'created_at', 'reference_no': 'reference_no', 'warehouse': 'warehouse_name',
    'category': 'category_name', 'brand': 'brand_name', 'count_type': 'count_type',
}


class CountConflict(APIException):
    status_code = 409
    default_detail = 'This count or its warehouse stock is being updated. Please retry.'
    default_code = 'stock_count_conflict'


def count_records(scope, *, query='', ordering='-created_at'):
    records = scoped_queryset(StockCount.objects.select_related('adjustment'), scope)
    if query:
        records = records.filter(
            Q(reference_no__icontains=query) | Q(warehouse_name__icontains=query)
            | Q(category_name__icontains=query) | Q(brand_name__icontains=query)
            | Q(count_type__icontains=query)
        )
    field = COUNT_ORDERING.get(ordering.removeprefix('-'))
    sort = ('-' if ordering.startswith('-') else '') + field if field else '-created_at'
    return records.order_by(sort, '-pk')


def count_options(scope):
    return {
        'warehouses': scoped_queryset(WareHouse.objects.filter(is_active=True), scope, 'pk').order_by('name', 'pk'),
        'categories': ProductCategory.objects.filter(is_active=True),
        'brands': Brand.objects.filter(is_active=True),
    }


def authorize(actor, permission):
    if not has_role_permissions(actor, permission):
        raise PermissionDenied('Your role does not have permission to perform this stock count action.')


def lock_count_warehouse(warehouse_id):
    records = WareHouse.objects.filter(pk=warehouse_id)
    if connection.vendor == 'sqlite':
        # Start with a write so concurrent stock writers cannot invalidate a SQLite read snapshot.
        records.update(is_active=F('is_active'))
    return records.select_for_update().first()


def save_header(now, **fields):
    reference = f"scr-{timezone.localtime(now):%Y%m%d-%H%M%S}"
    try:
        with transaction.atomic():
            return StockCount.objects.create(reference_no=reference, created_at=now, **fields)
    except IntegrityError:
        if not StockCount.objects.filter(reference_no=reference).exists():
            raise
    return StockCount.objects.create(reference_no=f'{reference}-{uuid4().hex[:16]}', created_at=now, **fields)


def with_stock_event(stocks, warehouse_id):
    latest = AuditLog.objects.filter(
        resource='warehouse.stock', warehouse_id=warehouse_id,
        object_id=Cast(OuterRef('pk'), CharField()),
    ).order_by('-pk').values('pk')[:1]
    return stocks.annotate(stock_event_id=Subquery(latest))


def create_stock_count(actor, *, warehouse_id, count_type, category_id=None, brand_id=None, blind_count=False):
    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            authorize(actor, ADD_COUNT)
            check_warehouse(scope, warehouse_id)
            warehouse = lock_count_warehouse(warehouse_id)
            if warehouse is None or not warehouse.is_active:
                raise ValidationError({'warehouse': 'Choose an active warehouse.'})
            if count_type not in StockCount.Type.values:
                raise ValidationError({'count_type': 'Choose a full or partial count.'})
            if count_type == StockCount.Type.FULL:
                category_id = brand_id = None
            elif not (category_id or brand_id):
                raise ValidationError({'count_type': 'Choose a category, a brand, or both for a partial count.'})

            stocks = Stock.objects.filter(warehouse=warehouse, product__status_id='active')
            category = brand = None
            if category_id:
                category = ProductCategory.objects.filter(pk=category_id, is_active=True).first()
                if category is None:
                    raise ValidationError({'category': 'Choose an active category.'})
                stocks = stocks.filter(product__category=category)
            if brand_id:
                brand = Brand.objects.filter(pk=brand_id, is_active=True).first()
                if brand is None:
                    raise ValidationError({'brand': 'Choose an active brand.'})
                stocks = stocks.filter(product__brand=brand)
            stocks = list(with_stock_event(stocks, warehouse_id).select_related('product__unit').order_by('product_id'))
            if not stocks:
                raise ValidationError({'warehouse': 'No active warehouse stock matches this count.'})

            username = actor.get_username()
            count = save_header(
                timezone.now(), warehouse=warehouse, warehouse_name=warehouse.name, count_type=count_type,
                category=category, category_name=category.name if category else '',
                brand=brand, brand_name=brand.name if brand else '', blind_count=blind_count,
                total_items=len(stocks), create_by=username, modified_by=username,
            )
            StockCountItem.objects.bulk_create([
                StockCountItem(
                    stock_count=count, product=stock.product, sku=stock.product.sku, product_name=stock.product.name,
                    unit=stock.product.unit, unit_code=stock.product.unit.code, currency_code=stock.product.currency_id,
                    expected_qty=stock.quantity, unit_cost=stock.product.cost_price,
                    source_stock_id=stock.pk, source_stock_updated_at=stock.updated_at,
                    source_stock_event_id=stock.stock_event_id,
                ) for stock in stocks
            ])
            return count
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise CountConflict() from exc
        raise


def normalize_sku(sku):
    return sku.strip().lower()


def match_counts(items, entries):
    exact = {item.sku.strip(): item for item in items}
    normalized = defaultdict(list)
    for item in items:
        normalized[normalize_sku(item.sku)].append(item)
    seen, errors = set(), []
    for entry in entries:
        label = f'Row {entry.row} ({entry.sku})'
        item = exact.get(entry.sku.strip())
        if item is None:
            matches = normalized[normalize_sku(entry.sku)]
            if len(matches) != 1:
                detail = 'Use the exact Product Code.' if matches else 'Product Code is outside this count.'
                errors.append(f'{label}: {detail}')
                continue
            item = matches[0]
        if item.pk in seen:
            errors.append(f'{label}: duplicate Product Code.')
            continue
        seen.add(item.pk)
        if entry.expected is not None and entry.expected != item.expected_qty:
            errors.append(f'{label}: Expected differs from this count’s snapshot.')
        item.counted_qty = entry.counted
    missing = [item.sku for item in items if item.pk not in seen]
    if missing:
        errors.append(f'{len(missing)} missing SKU(s): {", ".join(missing[:20])}.')
    if errors:
        raise ValidationError({'items': errors})


def movement_check(count, items):
    live = Stock.objects.select_for_update().filter(
        warehouse_id=count.warehouse_id, product_id__in=[item.product_id for item in items],
    ).order_by('product_id')
    by_product = {
        stock.product_id: (stock.pk, stock.updated_at, stock.quantity, stock.stock_event_id)
        for stock in with_stock_event(live, count.warehouse_id)
    }
    for item in items:
        item.stock_moved = by_product.get(item.product_id) != (
            item.source_stock_id, item.source_stock_updated_at, item.expected_qty, item.source_stock_event_id,
        )


def require_current_stock(items):
    stale = [item.sku for item in items if item.stock_moved]
    if stale:
        raise CountConflict({'items': [
            f'Stock moved after the snapshot for {", ".join(stale[:20])}. Start a fresh count before posting '
            'these variances.'
        ]}, code='stock_count_stale')


def create_variance_adjustment(actor, count, differences):
    username = actor.get_username()
    adjustment = StockAdjustment.objects.create(
        warehouse_id=count.warehouse_id, notes=f'Stock count {count.reference_no}',
        create_by=username, modified_by=username,
    )
    bulk_create_with_audit(StockAdjustmentItem.objects, [
        StockAdjustmentItem(
            adjustment=adjustment, line_number=number, product_id=item.product_id, unit_id=item.unit_id,
            operation=StockAdjustmentItem.Operation.ADD if item.variance_qty > 0 else StockAdjustmentItem.Operation.SUBTRACT,
            quantity=abs(item.variance_qty), create_by=username, modified_by=username,
        ) for number, item in enumerate(differences, start=1)
    ])
    return adjustment


def validate_count_adjustment(adjustment, changes):
    count = StockCount.objects.filter(adjustment=adjustment).first()
    if count is None:
        return
    if count.warehouse_id != adjustment.warehouse_id:
        raise ValidationError({'warehouse': 'Use the warehouse from the stock count.'})
    items = list(count.items.filter(product_id__in=[product_id for product_id, change in changes.items() if change]))
    movement_check(count, items)
    require_current_stock(items)


def finalize_stock_count(actor, count_id, entries, *, post_adjustment=False):
    from .adjustments import complete_adjustment

    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            authorize(actor, FINALIZE_COUNT)
            if post_adjustment:
                authorize(actor, POST_ADJUSTMENT)
            records = scoped_queryset(StockCount.objects.filter(pk=count_id), scope)
            pending = records.filter(status=StockCount.Status.PENDING)
            claimed = pending.update(status=StockCount.Status.PENDING)
            count = records.select_for_update().first()
            if count is None:
                raise NotFound('Stock count not found.')
            if not claimed:
                return count, False

            lock_count_warehouse(count.warehouse_id)
            items = list(count.items.all())
            match_counts(items, entries)
            movement_check(count, items)
            StockCountItem.objects.bulk_update(items, ['counted_qty', 'stock_moved'])
            if connection.vendor == 'sqlite':
                stored = dict(count.items.values_list('pk', 'counted_qty'))
                if any(stored[item.pk] != item.counted_qty for item in items):
                    raise ValidationError({'items': ['The database cannot store one or more counts accurately.']})

            differences = [item for item in items if item.variance_qty] if post_adjustment else []
            if differences:
                require_current_stock(differences)
                draft = create_variance_adjustment(actor, count, differences)
                count.adjustment, _ = complete_adjustment(actor, draft.pk)
            count.status = StockCount.Status.FINALIZED
            count.finalized_at = timezone.now()
            count.finalized_by = count.modified_by = actor.get_username()
            count.updated_at = count.finalized_at
            pending.update(adjustment=count.adjustment, status=count.status, finalized_at=count.finalized_at,
                           finalized_by=count.finalized_by, modified_by=count.modified_by, updated_at=count.updated_at)
            record_activity(count, 'finalized', actor=actor)
            return count, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise CountConflict() from exc
        raise


def create_finalized_count_adjustment(actor, count_id):
    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            authorize(actor, DRAFT_COUNT_ADJUSTMENT)
            records = scoped_queryset(StockCount.objects.filter(pk=count_id, status=StockCount.Status.FINALIZED), scope)
            if connection.vendor == 'sqlite':
                records.filter(adjustment__isnull=True).update(status=F('status'))
            count = records.select_for_update().first()
            if count is None:
                raise NotFound('Finalized stock count not found.')
            if count.adjustment_id:
                return False

            differences = list(count.items.filter(counted_qty__isnull=False).exclude(counted_qty=F('expected_qty')))
            if not differences:
                raise ValidationError('Counted and expected quantities match for every SKU.')

            count.adjustment = create_variance_adjustment(actor, count, differences)
            records.update(adjustment=count.adjustment, modified_by=actor.get_username(), updated_at=timezone.now())
            record_activity(count, 'adjustment_drafted', actor=actor)
            return True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise CountConflict() from exc
        raise


def summarize_count(items):
    quantity = defaultdict(lambda: dict(expected=Decimal(0), counted=Decimal(0), overage=Decimal(0),
                                        shortage=Decimal(0), variance=Decimal(0)))
    value = defaultdict(Decimal)
    with localcontext() as context:
        context.prec = 50
        for item in items:
            totals = quantity[item.unit_code]
            variance = item.variance_qty
            totals['expected'] += item.expected_qty
            totals['counted'] += item.counted_qty
            totals['variance'] += variance
            totals['overage'] += max(variance, 0)
            totals['shortage'] += max(-variance, 0)
            value[item.currency_code] += item.variance_value
    return {
        'matched_items': sum(item.variance_qty == 0 for item in items),
        'overage_items': sum(item.variance_qty > 0 for item in items),
        'shortage_items': sum(item.variance_qty < 0 for item in items),
        'moved_items': sum(item.stock_moved for item in items),
        'quantities': [{'unit': unit, **totals} for unit, totals in sorted(quantity.items())],
        'values': [{'currency': currency, 'variance': total} for currency, total in sorted(value.items())],
    }
