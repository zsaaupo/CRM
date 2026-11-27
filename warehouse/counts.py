from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.services import lock_rows
from inventory.models import Product
from .models import Stock, StockCount, StockCountLine, StockMovement, WareHouse
from .movements import post_valued_movements
from .scope import lock_warehouse_scope, scoped_queryset


class CountConflict(APIException):
    status_code = 409
    default_detail = 'Stock changed after this count began. Start a new count.'


def count_scope(user, action):
    scope = lock_warehouse_scope(user)
    if not has_role_permissions(user, ('warehouse.view_stock', 'warehouse.view_stockcount', f'warehouse.{action}_stockcount')):
        raise PermissionDenied('Your role cannot perform this count action.')
    return scope


def movement_cutoffs(warehouse, products):
    return dict(StockMovement.objects.filter(warehouse=warehouse, product_id__in=products)
                .order_by().values('product_id').annotate(last=Max('pk')).values_list('product_id', 'last'))


@transaction.atomic
def begin_count(user, *, warehouse, request_key, products, full_count, note):
    scope = count_scope(user, 'add')
    branch = get_object_or_404(lock_rows(scoped_queryset(WareHouse.objects.filter(pk=warehouse, is_active=True), scope, 'pk')))
    count, created = StockCount.objects.get_or_create(request_key=request_key, defaults=dict(
        warehouse=branch, full_count=full_count, note=note, create_by=user.get_username(), modified_by=user.get_username()))
    if not created:
        if count.warehouse_id != branch.pk or count.full_count != full_count or count.note != note or (
            not full_count and set(count.lines.values_list('product_id', flat=True)) != set(products)
        ):
            raise ValidationError({'request_key': 'This key was used for a different stock count.'})
        return count
    stocks = Stock.objects.filter(warehouse=branch)
    ids = list(stocks.values_list('product_id', flat=True)) if full_count else products
    units = dict(Product.objects.filter(pk__in=ids).values_list('pk', 'unit_id'))
    if len(units) != len(ids):
        raise ValidationError({'products': 'Choose existing products once each.'})
    expected = dict(stocks.filter(product_id__in=ids).values_list('product_id', 'quantity'))
    cutoffs = movement_cutoffs(branch, ids)
    StockCountLine.objects.bulk_create([StockCountLine(count=count, product_id=pk, unit_id=unit,
        expected=expected.get(pk, 0), last_movement=cutoffs.get(pk, 0)) for pk, unit in units.items()])
    return count


@transaction.atomic
def finalize_count(user, pk, quantities):
    scope = count_scope(user, 'change')
    count = get_object_or_404(lock_rows(scoped_queryset(StockCount.objects.filter(pk=pk), scope)))
    branch = get_object_or_404(lock_rows(WareHouse.objects.filter(pk=count.warehouse_id, is_active=True)))
    lines = list(count.lines.select_related('product').order_by('product_id'))
    if set(quantities) != {line.product_id for line in lines}:
        raise ValidationError({'lines': 'Enter the counted quantity for every product in this count.'})
    if count.finalized_at:
        if any(line.counted != quantities[line.product_id] for line in lines):
            raise CountConflict('This count was finalized with different quantities.')
        return count
    balances = Stock.objects.filter(warehouse=branch)
    if count.full_count and balances.exclude(product_id__in=quantities).exists():
        raise CountConflict()
    stocks = {row.product_id: row for row in lock_rows(balances.filter(product_id__in=quantities))}
    cutoffs = movement_cutoffs(branch, quantities)
    now = timezone.now()
    username = user.get_username()
    movements = []
    for line in lines:
        stock = stocks.get(line.product_id)
        if ((stock.quantity if stock else 0) != line.expected or line.unit_id != line.product.unit_id
                or line.last_movement != cutoffs.get(line.product_id, 0)):
            raise CountConflict()
        line.counted = quantities[line.product_id]
        variance = line.counted - line.expected
        if variance:
            stock = stock or Stock(warehouse=branch, product_id=line.product_id, create_by=username)
            stock.quantity = line.counted
            stock.modified_by = username
            stock.save()
            stock.refresh_from_db(fields=['quantity'])
            if stock.quantity != line.counted:
                raise ValidationError({'lines': 'The resulting stock quantity exceeds database precision.'})
            movements.append(StockMovement(warehouse=branch, product_id=line.product_id, unit_id=line.unit_id,
                quantity=variance, kind=StockMovement.Kind.COUNT, source_id=pk, source_line_id=line.pk,
                posting_key=f'count/{pk}/{line.pk}', posted_by=username, business_date=timezone.localdate(now)))
    post_valued_movements(branch.pk, movements)
    StockCountLine.objects.bulk_update(lines, ['counted'])
    count.finalized_at = now
    count.modified_by = username
    count.save(update_fields=['finalized_at', 'modified_by', 'updated_at'])
    return count
