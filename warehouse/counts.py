"""Physical stock count workflow with snapshot race guard (D-15)."""

import csv
from decimal import Decimal, InvalidOperation
from io import StringIO

from django.core.files.base import ContentFile
from django.db import OperationalError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.exceptions import StateConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.io_utils import parse_import_file
from common.models import AuditLog
from common.money import q_cost, q_qty
from inventory.models import Product
from .adjustments import _post_adjustment_inner
from .models import (
    Stock, StockAdjustment, StockAdjustmentItem, StockBatch, StockCount, StockCountLine, StockMovement, WareHouse,
)
from .scope import check_warehouse, lock_warehouse_scope, scoped_queryset
from .services import TransferBusy


COUNT_HEADERS = ['sku', 'name', 'batch', 'expected_qty', 'counted_qty']


def create_stock_count(
    actor,
    warehouse_id: int,
    count_type: str = StockCount.CountType.FULL,
    category_ids: list[int] | None = None,
    brand_ids: list[int] | None = None,
) -> StockCount:
    if not has_role_permissions(actor, ('warehouse.manage_stock_count',)):
        raise PermissionDenied('You do not have permission to create stock counts.')
    if count_type not in StockCount.CountType.values:
        raise ValidationError({'count_type': 'Choose full or partial.'})

    with transaction.atomic():
        scope = lock_warehouse_scope(actor)
        if not has_role_permissions(actor, ('warehouse.manage_stock_count',)):
            raise PermissionDenied('You do not have permission to create stock counts.')
        wh = WareHouse.objects.select_for_update().filter(pk=warehouse_id, is_active=True).first()
        if wh is None:
            raise ValidationError({'warehouse': 'Choose an active warehouse.'})
        check_warehouse(scope, wh.pk)

        now = timezone.now()
        username = actor.get_username()
        sc = StockCount.objects.create(
            warehouse=wh,
            count_type=count_type,
            snapshot_at=now,
            status=StockCount.Status.DRAFT,
            create_by=username,
            modified_by=username,
        )
        if category_ids:
            sc.categories.set(category_ids)
        if brand_ids:
            sc.brands.set(brand_ids)

        prod_qs = Product.objects.filter(product_type_id='goods')
        if count_type == StockCount.CountType.PARTIAL:
            if category_ids:
                prod_qs = prod_qs.filter(category_id__in=category_ids)
            if brand_ids:
                prod_qs = prod_qs.filter(brand_id__in=brand_ids)

        stocks = {
            s.product_id: s
            for s in Stock.objects.select_for_update().filter(warehouse=wh, product__in=prod_qs).select_related('product')
        }
        products = list(prod_qs.filter(pk__in=stocks.keys()).order_by('sku', 'pk'))

        csv_buf = StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow(COUNT_HEADERS)

        for prod in products:
            if prod.has_batch_expiry:
                batches = list(StockBatch.objects.filter(warehouse=wh, product=prod).order_by('batch_number', 'pk'))
                for batch in batches:
                    StockCountLine.objects.create(
                        stock_count=sc, product=prod, batch=batch,
                        expected_qty=batch.quantity, create_by=username, modified_by=username,
                    )
                    writer.writerow([prod.sku, prod.name, batch.batch_number, str(batch.quantity), ''])
                continue
            qty = stocks[prod.pk].quantity if prod.pk in stocks else Decimal('0')
            StockCountLine.objects.create(
                stock_count=sc, product=prod, batch=None,
                expected_qty=qty, create_by=username, modified_by=username,
            )
            writer.writerow([prod.sku, prod.name, '', str(qty), ''])

        sc.initial_file.save(f'{sc.reference_number}_initial.csv', ContentFile(csv_buf.getvalue().encode('utf-8-sig')), save=True)
        return sc


def import_count_final_file(actor, count_id: int, file_obj, filename: str = 'final.csv') -> StockCount:
    if not has_role_permissions(actor, ('warehouse.manage_stock_count',)):
        raise PermissionDenied('You do not have permission to import stock count files.')

    with transaction.atomic():
        scope = lock_warehouse_scope(actor)
        qs = scoped_queryset(StockCount.objects.all(), scope)
        qs.filter(pk=count_id).update(updated_at=F('updated_at'))
        sc = qs.select_for_update().filter(pk=count_id).first()
        if sc is None:
            raise NotFound('Stock count not found.')
        if sc.status == StockCount.Status.FINALIZED:
            raise StateConflict('A finalized stock count cannot be modified.')

        if isinstance(file_obj, str):
            raw_bytes = file_obj.encode('utf-8')
        elif hasattr(file_obj, 'read'):
            data = file_obj.read()
            raw_bytes = data.encode('utf-8') if isinstance(data, str) else bytes(data)
        else:
            raw_bytes = bytes(file_obj)

        headers, rows = parse_import_file(raw_bytes, filename=filename)
        missing = [c for c in COUNT_HEADERS if c not in headers]
        if missing:
            raise ValidationError({'file': f'Missing required headers: {", ".join(missing)}.'})

        lines = list(sc.lines.select_related('product', 'batch'))
        by_key = {(ln.product.sku, ln.batch.batch_number if ln.batch else ''): ln for ln in lines}

        errors = []
        updates = {}
        for row in rows:
            rnum = row['_row_number']
            sku = (row.get('sku') or '').strip()
            batch_no = (row.get('batch') or '').strip()
            line = by_key.get((sku, batch_no))
            if line is None:
                errors.append({'row': rnum, 'field': 'sku', 'error': f'SKU "{sku}" batch "{batch_no}" is not in this count.'})
                continue
            raw_qty = (row.get('counted_qty') or '').strip()
            if raw_qty == '':
                errors.append({'row': rnum, 'field': 'counted_qty', 'error': 'counted_qty is required.'})
                continue
            try:
                qty = Decimal(raw_qty)
            except (InvalidOperation, ValueError):
                errors.append({'row': rnum, 'field': 'counted_qty', 'error': f'Invalid quantity "{raw_qty}".'})
                continue
            if not qty.is_finite() or qty < 0:
                errors.append({'row': rnum, 'field': 'counted_qty', 'error': 'counted_qty cannot be negative.'})
                continue
            updates[line.pk] = q_qty(qty)

        if errors:
            raise ValidationError({'rows': errors})

        username = actor.get_username()
        for line in lines:
            if line.pk in updates:
                line.counted_qty = updates[line.pk]
                line.modified_by = username
                line.save(update_fields=['counted_qty', 'modified_by', 'updated_at'])

        sc.status = StockCount.Status.COUNTED
        sc.modified_by = username
        sc.final_file.save(f'{sc.reference_number}_final.csv', ContentFile(raw_bytes), save=True)
        return sc


def finalize_stock_count(actor, count_id: int, request_key, *, reconcile_intervening: bool = False):
    if not has_role_permissions(actor, ('warehouse.manage_stock_count',)):
        raise PermissionDenied('You do not have permission to finalize stock counts.')

    scope_key = f'stock_count_finalize:{count_id}'

    def _loader(rec):
        return StockCount.objects.get(pk=rec.result_id)

    def _do():
        try:
            with transaction.atomic():
                scope = lock_warehouse_scope(actor)
                if not has_role_permissions(actor, ('warehouse.manage_stock_count',)):
                    raise PermissionDenied('You do not have permission to finalize stock counts.')

                qs = scoped_queryset(StockCount.objects.all(), scope)
                qs.filter(pk=count_id).update(updated_at=F('updated_at'))
                sc = qs.select_for_update().filter(pk=count_id).first()
                if sc is None:
                    raise NotFound('Stock count not found.')
                if sc.status == StockCount.Status.FINALIZED:
                    return IdempotentResult(
                        result_type='stock_count', result_id=sc.pk,
                        result_data={'id': sc.pk, 'status': sc.status}, value=sc,
                    )
                if sc.status == StockCount.Status.DRAFT:
                    raise ValidationError({'stock_count': 'Import the final counted CSV before finalizing.'})

                lines = list(sc.lines.select_related('product', 'batch').order_by('product_id', 'pk'))
                missing = [ln.product.sku for ln in lines if ln.counted_qty is None]
                if missing:
                    raise ValidationError({'lines': f'Missing counted_qty for SKUs: {", ".join(missing)}.'})

                # Lock stock rows (③)
                Stock.objects.filter(warehouse_id=sc.warehouse_id, product_id__in=[ln.product_id for ln in lines]).update(
                    updated_at=F('updated_at'),
                )
                stocks = {
                    s.product_id: s
                    for s in Stock.objects.select_for_update().filter(
                        warehouse_id=sc.warehouse_id, product_id__in=[ln.product_id for ln in lines],
                    )
                }

                # Compute intervening movements after snapshot_at
                recount_needed = []
                username = actor.get_username()
                for line in lines:
                    mv_qs = StockMovement.objects.filter(
                        warehouse_id=sc.warehouse_id,
                        product_id=line.product_id,
                        posted_at__gt=sc.snapshot_at,
                    )
                    if line.batch_id is not None:
                        mv_qs = mv_qs.filter(batch_id=line.batch_id)
                    moved = mv_qs.aggregate(total=Sum('quantity_delta'))['total'] or Decimal('0')
                    line.movement_since_snapshot = moved
                    if moved != Decimal('0') and not reconcile_intervening:
                        recount_needed.append({
                            'sku': line.product.sku,
                            'expected_qty': str(line.expected_qty),
                            'movement_since_snapshot': str(moved),
                        })

                if recount_needed:
                    raise StateConflict({
                        'detail': 'Stock moved after the count snapshot was taken.',
                        'code': 'needs_recount',
                        'needs_recount': True,
                        'lines': recount_needed,
                    })

                variances = []
                for line in lines:
                    basis = line.expected_qty + (line.movement_since_snapshot if reconcile_intervening else Decimal('0'))
                    var = q_qty(line.counted_qty - basis)
                    line.variance_qty = var
                    line.needs_recount = False
                    line.modified_by = username
                    line.save(update_fields=['movement_since_snapshot', 'variance_qty', 'needs_recount', 'modified_by', 'updated_at'])
                    if var != Decimal('0'):
                        variances.append((line, var))

                if variances:
                    adj = StockAdjustment.objects.create(
                        warehouse_id=sc.warehouse_id,
                        adjustment_date=timezone.localdate(),
                        reason=f'stock count {sc.reference_number}',
                        note=f'Generated from physical stock count {sc.reference_number}.',
                        create_by=username,
                        modified_by=username,
                    )
                    for line, var in variances:
                        action = StockAdjustmentItem.Action.ADDITION if var > 0 else StockAdjustmentItem.Action.SUBTRACTION
                        stock_row = stocks.get(line.product_id)
                        avg_cost = q_cost(
                            (stock_row.average_cost if stock_row and stock_row.average_cost else line.product.cost_price) or Decimal('0')
                        )
                        StockAdjustmentItem.objects.create(
                            adjustment=adj,
                            product=line.product,
                            batch=line.batch,
                            action=action,
                            quantity=abs(var),
                            unit=line.product.unit,
                            unit_cost=avg_cost if action == StockAdjustmentItem.Action.ADDITION else None,
                            create_by=username,
                            modified_by=username,
                        )
                    _post_adjustment_inner(actor, adj.pk, request_key, bypass_perm=True)
                    sc.adjustment = adj

                sc.reconcile_intervening = reconcile_intervening
                sc.status = StockCount.Status.FINALIZED
                sc.modified_by = username
                sc.save(update_fields=['adjustment', 'reconcile_intervening', 'status', 'modified_by', 'updated_at'])
                log_audit_event(
                    actor, AuditLog.Action.POST, sc,
                    changes={'status': sc.status, 'adjustment_id': sc.adjustment_id, 'reconcile_intervening': reconcile_intervening},
                    request_key=request_key, warehouse=sc.warehouse,
                )
                return IdempotentResult(
                    result_type='stock_count', result_id=sc.pk,
                    result_data={'id': sc.pk, 'status': sc.status, 'adjustment_id': sc.adjustment_id},
                    value=sc,
                )
        except OperationalError as exc:
            if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
                raise TransferBusy() from exc
            raise

    return run_idempotent(
        request_key, scope_key,
        {'count_id': count_id, 'reconcile_intervening': reconcile_intervening},
        _do, loader=_loader,
    )
