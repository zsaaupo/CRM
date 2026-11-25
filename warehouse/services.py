"""Atomic warehouse transfers and append-only stock movement ledger."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.exceptions import IdempotencyConflict, InsufficientStock
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog
from common.money import q_cost, q_qty, q_total_cost
from common.settings_store import get_general
from inventory.models import Product, TaxRule, UnitOfMeasure, require_convertible_unit
from .models import (
    Stock, StockBatch, StockMovement, StockSerial, WareHouse, WarehouseTransfer, WarehouseTransferItem,
)
from .scope import all_warehouse_access, check_warehouse, lock_warehouse_scope, scoped_queryset, warehouse_scope


MAX_STOCK_QUANTITY = Decimal('999999999999.999999')
ZERO = Decimal('0')

INBOUND_KINDS = {
    StockMovement.Kind.OPENING,
    StockMovement.Kind.PURCHASE_RECEIPT,
    StockMovement.Kind.SALE_RETURN,
    StockMovement.Kind.TRANSFER_IN,
    StockMovement.Kind.ADJUSTMENT_ADD,
    StockMovement.Kind.PRODUCTION_OUTPUT,
}
OUTBOUND_KINDS = {
    StockMovement.Kind.PURCHASE_RETURN,
    StockMovement.Kind.SALE_ISSUE,
    StockMovement.Kind.TRANSFER_OUT,
    StockMovement.Kind.ADJUSTMENT_SUB,
    StockMovement.Kind.PRODUCTION_CONSUME,
}


class TransferCompleted(APIException):
    status_code = 409
    default_detail = 'Completed transfers are read-only and cannot be changed or deleted.'
    default_code = 'transfer_completed'


class TransferBusy(APIException):
    status_code = 409
    default_detail = 'This transfer or its stock is being updated. Retry the request; a transfer only moves stock once.'
    default_code = 'transfer_busy'


def post_stock_movement(
    *,
    warehouse_id,
    product_id,
    kind,
    quantity_delta,
    unit_cost_base=None,
    batch_id=None,
    serial_ids=None,
    serial_status=None,
    movement_date=None,
    reference_type,
    reference_id,
    reference_line_id=None,
    posting_key,
    notes='',
    actor=None,
):
    """Single atomic writer for Stock balances and append-only StockMovement rows."""
    if not connection.in_atomic_block:
        raise RuntimeError('post_stock_movement must be called inside transaction.atomic().')

    delta = q_qty(quantity_delta)
    if delta == ZERO:
        raise ValidationError({'quantity': 'Stock movement quantity cannot be zero.'})
    if kind in INBOUND_KINDS and delta < ZERO:
        raise ValidationError({'quantity': f'Inbound movement {kind} requires a positive quantity.'})
    if kind in OUTBOUND_KINDS and delta > ZERO:
        raise ValidationError({'quantity': f'Outbound movement {kind} requires a negative quantity.'})
    if kind not in INBOUND_KINDS and kind not in OUTBOUND_KINDS:
        raise ValidationError({'kind': 'Invalid stock movement kind.'})

    existing = StockMovement.objects.filter(posting_key=posting_key).first()
    if existing is not None:
        if (
            existing.warehouse_id != warehouse_id
            or existing.product_id != product_id
            or existing.kind != kind
            or existing.quantity_delta != delta
            or existing.batch_id != batch_id
        ):
            raise IdempotencyConflict('Posting key already used with different movement details.')
        return existing

    username = actor.get_username() if actor is not None else ''
    now = timezone.now()
    m_date = movement_date or timezone.localdate(now)

    # Lock Stock row (③) in SQLite-safe manner
    Stock.objects.filter(warehouse_id=warehouse_id, product_id=product_id).update(updated_at=F('updated_at'))
    stock = Stock.objects.select_for_update().filter(warehouse_id=warehouse_id, product_id=product_id).first()
    product = Product.objects.select_for_update().get(pk=product_id)
    allow_negative = bool(get_general().allow_sale_without_stock)

    if stock is None:
        if delta < ZERO and not allow_negative:
            wh_name = WareHouse.objects.filter(pk=warehouse_id).values_list('name', flat=True).first() or warehouse_id
            raise InsufficientStock({'quantity': f'Insufficient stock for {product.sku} in {wh_name}.'})
        stock, _ = Stock.objects.get_or_create(
            warehouse_id=warehouse_id,
            product_id=product_id,
            defaults={
                'quantity': ZERO,
                'oversold_quantity': ZERO,
                'average_cost': q_cost(product.cost_price or ZERO),
                'create_by': username or 'system',
                'modified_by': username or 'system',
            },
        )

    old_net = stock.quantity - stock.oversold_quantity
    old_avg = stock.average_cost or ZERO
    new_net = old_net + delta

    if new_net > MAX_STOCK_QUANTITY:
        raise ValidationError({'quantity': 'The resulting stock quantity exceeds the supported stock limit.'})

    with localcontext() as ctx:
        ctx.prec = 50
        if delta > ZERO:
            in_qty = delta
            in_cost = q_cost(unit_cost_base if unit_cost_base is not None else (old_avg or product.cost_price or ZERO))
            if old_net <= ZERO:
                new_avg = in_cost
            else:
                new_avg = q_cost((old_net * old_avg + in_qty * in_cost) / (old_net + in_qty))
            applied_unit_cost = in_cost
            stock.average_cost = new_avg
        else:
            out_qty = -delta
            if new_net < ZERO and not allow_negative:
                wh_name = WareHouse.objects.filter(pk=warehouse_id).values_list('name', flat=True).first() or warehouse_id
                raise InsufficientStock({'quantity': f'Insufficient stock for {product.sku} in {wh_name}.'})

            if kind == StockMovement.Kind.PURCHASE_RETURN:
                out_cost = q_cost(unit_cost_base if unit_cost_base is not None else old_avg)
                if new_net > ZERO:
                    raw_avg = (old_net * old_avg - out_qty * out_cost) / new_net
                    stock.average_cost = q_cost(max(raw_avg, ZERO))
                applied_unit_cost = out_cost
            else:
                if unit_cost_base is not None:
                    applied_unit_cost = q_cost(unit_cost_base)
                elif old_net > ZERO or old_avg > ZERO:
                    applied_unit_cost = q_cost(old_avg)
                else:
                    applied_unit_cost = q_cost(product.cost_price or ZERO)

    if new_net >= ZERO:
        stock.quantity = new_net
        stock.oversold_quantity = ZERO
    else:
        stock.quantity = ZERO
        stock.oversold_quantity = -new_net

    Stock.objects.filter(pk=stock.pk).update(
        quantity=stock.quantity,
        oversold_quantity=stock.oversold_quantity,
        average_cost=stock.average_cost,
        modified_by=username or stock.modified_by,
        updated_at=now,
    )

    if batch_id is not None:
        StockBatch.objects.filter(pk=batch_id, warehouse_id=warehouse_id, product_id=product_id).update(updated_at=F('updated_at'))
        batch = StockBatch.objects.select_for_update().filter(
            pk=batch_id, warehouse_id=warehouse_id, product_id=product_id,
        ).first()
        if batch is None:
            raise ValidationError({'batch': 'Choose a batch belonging to this product and warehouse.'})
        new_batch_qty = batch.quantity + delta
        if new_batch_qty < ZERO:
            raise InsufficientStock({'batch': f'Batch {batch.batch_number} only has {batch.quantity} available.'})
        batch.quantity = new_batch_qty
        batch.modified_by = username or batch.modified_by
        batch.updated_at = now
        batch.save(update_fields=['quantity', 'modified_by', 'updated_at'])

    if serial_ids:
        target_status = serial_status or (StockSerial.Status.IN_STOCK if delta > ZERO else StockSerial.Status.SOLD)
        serial_qs = StockSerial.objects.select_for_update().filter(pk__in=serial_ids, product_id=product_id)
        for serial in serial_qs:
            serial.status = target_status
            serial.warehouse_id = warehouse_id
            if batch_id is not None:
                serial.batch_id = batch_id
            serial.modified_by = username or serial.modified_by
            serial.updated_at = now
            serial.save(update_fields=['status', 'warehouse_id', 'batch_id', 'modified_by', 'updated_at'])

    total_cost = q_total_cost(abs(delta) * applied_unit_cost)
    return StockMovement.objects.create(
        warehouse_id=warehouse_id,
        product_id=product_id,
        batch_id=batch_id,
        kind=kind,
        quantity_delta=delta,
        unit_cost_base=applied_unit_cost,
        total_cost_base=total_cost,
        balance_after=new_net,
        movement_date=m_date,
        posted_at=now,
        reference_type=reference_type,
        reference_id=reference_id,
        reference_line_id=reference_line_id,
        posting_key=posting_key,
        notes=notes,
        create_by=username or 'system',
        modified_by=username or 'system',
    )


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
    if record.is_completed or record.status != WarehouseTransfer.Status.PENDING:
        raise TransferCompleted()
    return record


def _dispatch_transfer_inner(actor, record: WarehouseTransfer, now, *, request_key=None, update_lines: bool = True):
    if record.source_warehouse_id == record.destination_warehouse_id:
        raise ValidationError({'destination_warehouse': 'Choose a different destination warehouse.'})
    warehouses = list(WareHouse.objects.select_for_update().filter(
        pk__in=[record.source_warehouse_id, record.destination_warehouse_id]).order_by('pk'))
    if len(warehouses) != 2 or any(not warehouse.is_active for warehouse in warehouses):
        raise ValidationError({'warehouse': 'Both warehouses must be active before dispatching a transfer.'})
    items = list(record.items.select_related('product', 'unit', 'batch').order_by('product_id', 'line_number', 'pk'))
    if not items:
        raise ValidationError({'items': 'Add at least one product before dispatching the transfer.'})
    products = {
        product.pk: product
        for product in Product.objects.select_for_update().prefetch_related('combo_items').filter(
            pk__in={item.product_id for item in items}).order_by('pk')
    }
    from .tracking import allocate_tracking
    username = actor.get_username()
    for item in items:
        product = products.get(item.product_id)
        if product is None:
            raise ValidationError({'items': f'Line {item.line_number}: product missing.'})
        factor = require_convertible_unit(item.unit, product.unit, field='items')
        if not item.quantity.is_finite() or item.quantity <= 0:
            raise ValidationError({'items': 'Every transfer quantity must be positive.'})
        base_qty = q_qty(item.quantity * factor)
        if base_qty > MAX_STOCK_QUANTITY:
            raise ValidationError({'quantity': 'The quantity exceeds the supported stock limit.'})
        item.unit_conversion_factor = factor
        item.base_quantity = base_qty
        if product.product_type_id in ('service', 'digital'):
            item.sent_qty = base_qty
            if update_lines:
                item.save(update_fields=['unit_conversion_factor', 'base_quantity', 'sent_qty', 'updated_at'])
            continue
        if product.product_type_id == 'combo':
            total_cogs = Decimal('0')
            for comp in product.combo_items.select_related('component_product').order_by('component_product_id'):
                if comp.component_product.product_type_id in ('service', 'digital'):
                    continue
                comp_qty = q_qty(base_qty * comp.quantity)
                out_move = post_stock_movement(
                    warehouse_id=record.source_warehouse_id,
                    product_id=comp.component_product_id,
                    kind=StockMovement.Kind.TRANSFER_OUT,
                    quantity_delta=-comp_qty,
                    movement_date=record.transfer_date,
                    reference_type='warehouse_transfer',
                    reference_id=record.pk,
                    reference_line_id=item.pk,
                    posting_key=f'transfer:{record.pk}:out:{item.pk}:comp:{comp.component_product_id}',
                    actor=actor,
                )
                total_cogs += Decimal(out_move.total_cost_base or 0)
            item.unit_cost = q_cost(total_cogs / base_qty) if base_qty else Decimal('0')
            item.sent_qty = base_qty
            if update_lines:
                item.save(update_fields=['unit_conversion_factor', 'base_quantity', 'unit_cost', 'sent_qty', 'updated_at'])
            continue
        batch_obj, serial_objs = allocate_tracking(
            product, record.source_warehouse_id, base_qty,
            batch_id=item.batch_id, serials=item.serial_numbers, doc_date=record.transfer_date,
        )
        out_move = post_stock_movement(
            warehouse_id=record.source_warehouse_id,
            product_id=item.product_id,
            kind=StockMovement.Kind.TRANSFER_OUT,
            quantity_delta=-base_qty,
            batch_id=batch_obj.pk if batch_obj else None,
            serial_ids=[s.pk for s in serial_objs],
            serial_status=StockSerial.Status.IN_TRANSIT,
            movement_date=record.transfer_date,
            reference_type='warehouse_transfer',
            reference_id=record.pk,
            reference_line_id=item.pk,
            posting_key=f'transfer:{record.pk}:out:{item.pk}',
            actor=actor,
        )
        item.unit_cost = out_move.unit_cost_base or Decimal('0')
        item.sent_qty = base_qty
        if update_lines:
            item.save(update_fields=['unit_conversion_factor', 'base_quantity', 'unit_cost', 'sent_qty', 'updated_at'])

    record.status = WarehouseTransfer.Status.SENT
    record.sent_at = now
    record.sent_by = actor
    record.modified_by = username
    record.save(update_fields=['status', 'sent_at', 'sent_by', 'modified_by', 'updated_at'])
    log_audit_event(
        actor, AuditLog.Action.POST, record,
        changes={'event': 'dispatch', 'status': record.status},
        request_key=request_key, warehouse=record.source_warehouse,
    )
    return items


def _receive_transfer_inner(actor, record: WarehouseTransfer, now, *, lines=None, allow_partial=False, request_key=None, update_lines: bool = True, prefetched_items=None):
    warehouses = list(WareHouse.objects.select_for_update().filter(
        pk__in=[record.source_warehouse_id, record.destination_warehouse_id]).order_by('pk'))
    if len(warehouses) != 2 or any(not warehouse.is_active for warehouse in warehouses):
        raise ValidationError({'warehouse': 'Both warehouses must be active before receiving a transfer.'})
    items = prefetched_items if prefetched_items is not None else list(
        record.items.select_related('product', 'unit', 'batch').order_by('product_id', 'line_number', 'pk')
    )
    products = {
        p.pk: p
        for p in Product.objects.select_for_update().prefetch_related('combo_items').filter(
            pk__in={it.product_id for it in items}).order_by('pk')
    }
    by_line = {it.pk: it for it in items}
    to_receive: list[tuple[WarehouseTransferItem, Decimal]] = []

    if lines is None:
        for it in items:
            rem = q_qty(Decimal(it.sent_qty or 0) - Decimal(it.received_qty or 0))
            if rem > 0:
                to_receive.append((it, rem))
    else:
        seen = set()
        for entry in lines:
            lid = entry.get('line_id') or entry.get('id')
            if lid in seen:
                raise ValidationError({'lines': 'Duplicate transfer line in receive payload.'})
            seen.add(lid)
            it = by_line.get(lid)
            if it is None:
                raise ValidationError({'lines': f'Transfer line #{lid} not found.'})
            qty = q_qty(entry.get('quantity') or 0)
            rem = q_qty(Decimal(it.sent_qty or 0) - Decimal(it.received_qty or 0))
            if qty <= 0 or qty > rem:
                raise ValidationError({'lines': f'Line {it.line_number}: receive quantity must be in (0, {rem}].'})
            to_receive.append((it, qty))

        will_stay_partial = any(
            q_qty((it.received_qty or Decimal('0')) + dict((x.pk, q) for x, q in to_receive).get(it.pk, Decimal('0')))
            < (it.sent_qty or Decimal('0'))
            for it in items
        )
        if will_stay_partial and not allow_partial:
            raise ValidationError({'lines': 'Partial receipt requires allow_partial=true.'})

    if not to_receive:
        return

    username = actor.get_username()
    for item, recv_qty in to_receive:
        product = products[item.product_id]
        if product.product_type_id in ('service', 'digital'):
            item.received_qty = q_qty((item.received_qty or Decimal('0')) + recv_qty)
            if update_lines:
                item.save(update_fields=['received_qty', 'updated_at'])
            continue
        if product.product_type_id == 'combo':
            for comp in product.combo_items.select_related('component_product').order_by('component_product_id'):
                if comp.component_product.product_type_id in ('service', 'digital'):
                    continue
                comp_qty = q_qty(recv_qty * comp.quantity)
                post_stock_movement(
                    warehouse_id=record.destination_warehouse_id,
                    product_id=comp.component_product_id,
                    kind=StockMovement.Kind.TRANSFER_IN,
                    quantity_delta=comp_qty,
                    unit_cost_base=None,
                    movement_date=record.transfer_date,
                    reference_type='warehouse_transfer',
                    reference_id=record.pk,
                    reference_line_id=item.pk,
                    posting_key=f'transfer:{record.pk}:in:{item.pk}:{item.received_qty}:comp:{comp.component_product_id}',
                    actor=actor,
                )
            item.received_qty = q_qty((item.received_qty or Decimal('0')) + recv_qty)
            if update_lines:
                item.save(update_fields=['received_qty', 'updated_at'])
            continue

        dest_batch_id = None
        if item.batch_id:
            src_batch = item.batch
            dest_batch, _ = StockBatch.objects.get_or_create(
                warehouse_id=record.destination_warehouse_id,
                product_id=item.product_id,
                batch_number=src_batch.batch_number,
                defaults={
                    'expiry_date': src_batch.expiry_date,
                    'quantity': Decimal('0'),
                    'unit_cost': item.unit_cost or src_batch.unit_cost,
                    'create_by': username,
                    'modified_by': username,
                },
            )
            dest_batch_id = dest_batch.pk
        serial_ids = []
        if product.has_imei_serial and item.serial_numbers:
            serial_ids = list(
                StockSerial.objects.filter(
                    product_id=item.product_id, serial_number__in=item.serial_numbers,
                ).values_list('pk', flat=True)
            )
        post_stock_movement(
            warehouse_id=record.destination_warehouse_id,
            product_id=item.product_id,
            kind=StockMovement.Kind.TRANSFER_IN,
            quantity_delta=recv_qty,
            unit_cost_base=item.unit_cost,
            batch_id=dest_batch_id,
            serial_ids=serial_ids,
            serial_status=StockSerial.Status.IN_STOCK,
            movement_date=record.transfer_date,
            reference_type='warehouse_transfer',
            reference_id=record.pk,
            reference_line_id=item.pk,
            posting_key=f'transfer:{record.pk}:in:{item.pk}:{item.received_qty}',
            actor=actor,
        )
        item.received_qty = q_qty((item.received_qty or Decimal('0')) + recv_qty)
        if update_lines:
            item.save(update_fields=['received_qty', 'updated_at'])

    if all((it.received_qty or Decimal('0')) >= (it.sent_qty or Decimal('0')) for it in items):
        record.status = WarehouseTransfer.Status.COMPLETED
        record.is_completed = True
        record.received_at = now
        record.received_by = actor
    record.modified_by = username
    record.save(update_fields=['status', 'is_completed', 'received_at', 'received_by', 'modified_by', 'updated_at'])
    log_audit_event(
        actor, AuditLog.Action.RECEIVE, record,
        changes={'event': 'receive', 'status': record.status},
        request_key=request_key, warehouse=record.destination_warehouse,
    )


def dispatch_transfer(actor, transfer_id: int, request_key=None):
    if not (
        has_role_permissions(actor, ('warehouse.dispatch_transfer',))
        or has_role_permissions(actor, ('warehouse.change_warehousetransfer',))
    ):
        raise PermissionDenied('You do not have permission to dispatch warehouse transfers.')
    try:
        with transaction.atomic(), localcontext() as context:
            context.prec = 50
            scope = lock_warehouse_scope(actor)
            if not (
                has_role_permissions(actor, ('warehouse.dispatch_transfer',))
                or has_role_permissions(actor, ('warehouse.change_warehousetransfer',))
            ):
                raise PermissionDenied('You do not have permission to dispatch warehouse transfers.')
            records = transfer_queryset(
                WarehouseTransfer.objects.prefetch_related('items__product', 'items__unit'), scope, write=False,
            )
            now = timezone.now()
            records.filter(pk=transfer_id).update(updated_at=F('updated_at'))
            record = records.select_for_update().filter(pk=transfer_id).first()
            if record is None:
                raise NotFound('Warehouse transfer not found.')
            check_warehouse(scope, record.source_warehouse_id)
            if record.status != WarehouseTransfer.Status.PENDING:
                return record, False
            _dispatch_transfer_inner(actor, record, now, request_key=request_key)
            return record, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise TransferBusy() from exc
        raise
    except IntegrityError as exc:
        raise TransferBusy('The transfer could not be dispatched. Check its stock references and retry.') from exc


def receive_transfer(actor, transfer_id: int, request_key=None, *, lines=None, allow_partial: bool = False):
    if not (
        has_role_permissions(actor, ('warehouse.receive_transfer',))
        or has_role_permissions(actor, ('warehouse.change_warehousetransfer',))
    ):
        raise PermissionDenied('You do not have permission to receive warehouse transfers.')
    try:
        with transaction.atomic(), localcontext() as context:
            context.prec = 50
            scope = lock_warehouse_scope(actor)
            if not (
                has_role_permissions(actor, ('warehouse.receive_transfer',))
                or has_role_permissions(actor, ('warehouse.change_warehousetransfer',))
            ):
                raise PermissionDenied('You do not have permission to receive warehouse transfers.')
            records = transfer_queryset(
                WarehouseTransfer.objects.prefetch_related('items__product', 'items__unit'), scope, write=False,
            )
            now = timezone.now()
            records.filter(pk=transfer_id).update(updated_at=F('updated_at'))
            record = records.select_for_update().filter(pk=transfer_id).first()
            if record is None:
                raise NotFound('Warehouse transfer not found.')
            check_warehouse(scope, record.destination_warehouse_id)
            if record.status == WarehouseTransfer.Status.COMPLETED:
                return record, False
            if record.status != WarehouseTransfer.Status.SENT:
                raise ValidationError({'status': 'Dispatch the transfer before receiving it.'})
            _receive_transfer_inner(actor, record, now, lines=lines, allow_partial=allow_partial, request_key=request_key)
            return record, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise TransferBusy() from exc
        raise
    except IntegrityError as exc:
        raise TransferBusy('The transfer could not be received. Check its stock references and retry.') from exc


def complete_transfer(actor, transfer_id):
    require_transfer_permission(actor, 'change')
    try:
        with transaction.atomic(), localcontext() as context:
            context.prec = 50
            scope = lock_warehouse_scope(actor)
            require_transfer_permission(actor, 'change')
            records = transfer_queryset(
                WarehouseTransfer.objects.prefetch_related('items__product', 'items__unit'), scope, write=True,
            )
            now = timezone.now()
            records.filter(pk=transfer_id).update(updated_at=F('updated_at'))
            try:
                record = records.select_for_update().get(pk=transfer_id)
            except WarehouseTransfer.DoesNotExist as exc:
                raise NotFound('Warehouse transfer not found.') from exc
            if record.is_completed or record.status == WarehouseTransfer.Status.COMPLETED:
                return record, False
            if record.status == WarehouseTransfer.Status.PENDING:
                dispatched_items = _dispatch_transfer_inner(actor, record, now, update_lines=False)
                _receive_transfer_inner(
                    actor, record, now, update_lines=False, prefetched_items=dispatched_items,
                )
            else:
                _receive_transfer_inner(actor, record, now)
            return record, True
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise TransferBusy() from exc
        raise
    except IntegrityError as exc:
        raise TransferBusy('The transfer could not be completed. Check its stock references and retry.') from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The resulting quantity exceeds database precision. Stock was not transferred.'}) from exc


def import_transfer_csv(actor, *, source_warehouse_id: int, destination_warehouse_id: int,
                        csv_content, filename: str = 'transfer.csv',
                        status: str = WarehouseTransfer.Status.PENDING,
                        shipping_cost: Decimal = Decimal('0'),
                        transfer_date=None, notes: str = '', attachment=None):
    from common.io_utils import parse_import_file
    if not (
        has_role_permissions(actor, ('warehouse.import_stock',))
        or has_role_permissions(actor, ('warehouse.add_warehousetransfer',))
    ):
        raise PermissionDenied('You do not have permission to import warehouse transfers.')
    if status not in (WarehouseTransfer.Status.PENDING, WarehouseTransfer.Status.SENT):
        raise ValidationError({'status': 'Imported transfer initial status must be pending or sent.'})

    with transaction.atomic():
        scope = lock_warehouse_scope(actor)
        check_transfer_warehouses(scope, source_warehouse_id, destination_warehouse_id)
        if source_warehouse_id == destination_warehouse_id:
            raise ValidationError({'destination_warehouse': 'Choose a different warehouse from the source.'})

        headers, rows = parse_import_file(csv_content, filename=filename)
        required = ['product_code', 'quantity', 'product_unit', 'product_cost', 'tax_name']
        missing = [c for c in required if c not in headers]
        if missing:
            raise ValidationError({'file': f'Missing required headers: {", ".join(missing)}.'})
        if not rows:
            raise ValidationError({'file': 'Add at least one row to import.'})

        parsed = []
        errors = []
        for row in rows:
            rnum = row['_row_number']
            sku = (row.get('product_code') or '').strip()
            product = Product.objects.filter(sku=sku).first()
            if product is None:
                errors.append({'row': rnum, 'field': 'product_code', 'error': f'Unknown product SKU "{sku}".'})
                continue
            unit_code = (row.get('product_unit') or '').strip()
            unit = UnitOfMeasure.objects.filter(code=unit_code).first()
            if unit is None:
                errors.append({'row': rnum, 'field': 'product_unit', 'error': f'Unknown unit code "{unit_code}".'})
                continue
            try:
                factor = require_convertible_unit(unit, product.unit, field='product_unit')
                qty = q_qty(row.get('quantity') or 0)
                if qty <= 0:
                    raise ValidationError('Quantity must be positive.')
                cost = Decimal(row.get('product_cost') or '0')
                if cost < 0:
                    raise ValidationError('Cost cannot be negative.')
            except Exception as exc:
                errors.append({'row': rnum, 'field': 'row', 'error': str(exc)})
                continue
            tax_name = (row.get('tax_name') or '').strip()
            tax_rate = Decimal('0')
            if tax_name and tax_name.lower() not in ('none', 'no tax', 'n/a', '0'):
                tr = TaxRule.objects.filter(name=tax_name).first()
                if tr is None:
                    errors.append({'row': rnum, 'field': 'tax_name', 'error': f'Unknown tax rule "{tax_name}".'})
                    continue
                tax_rate = Decimal(tr.rate)
            line_sub = q_total_cost(qty * cost)
            line_tax = q_total_cost(line_sub * tax_rate / Decimal('100'))
            parsed.append({
                'product': product, 'unit': unit, 'factor': factor,
                'quantity': qty, 'unit_cost': cost, 'tax_rate': tax_rate,
                'tax_amount': line_tax, 'subtotal': line_sub + line_tax,
            })

        if errors:
            raise ValidationError({'rows': errors})

        username = actor.get_username()
        record = WarehouseTransfer.objects.create(
            source_warehouse_id=source_warehouse_id,
            destination_warehouse_id=destination_warehouse_id,
            transfer_date=transfer_date or timezone.localdate(),
            notes=notes,
            shipping_cost=shipping_cost or Decimal('0'),
            create_by=username,
            modified_by=username,
        )
        if attachment is not None:
            record.attachment = attachment
            record.save(update_fields=['attachment'])
        for idx, item in enumerate(parsed, start=1):
            WarehouseTransferItem.objects.create(
                transfer=record,
                line_number=idx,
                product=item['product'],
                unit=item['unit'],
                quantity=item['quantity'],
                unit_conversion_factor=item['factor'],
                base_quantity=q_qty(item['quantity'] * item['factor']),
                unit_cost=item['unit_cost'],
                tax_rate=item['tax_rate'],
                tax_amount=item['tax_amount'],
                subtotal=item['subtotal'],
                create_by=username,
                modified_by=username,
            )
        if status == WarehouseTransfer.Status.SENT:
            _dispatch_transfer_inner(actor, record, timezone.now())
        log_audit_event(
            actor, AuditLog.Action.IMPORT, record,
            changes={'status': record.status, 'line_count': len(parsed)},
            warehouse=record.source_warehouse,
        )
        return record


def stock_valuation_chart(actor, *, as_of=None) -> dict:
    from django.db.models import F as _F, Sum as _Sum
    scope = warehouse_scope(actor)
    whs = scoped_queryset(WareHouse.objects.filter(is_active=True).order_by('name', 'pk'), scope, 'pk')
    wh_map = {w.pk: w for w in whs}
    rows = []

    if as_of is None:
        in_transit_qs = WarehouseTransferItem.objects.filter(
            transfer__status=WarehouseTransfer.Status.SENT,
            transfer__destination_warehouse_id__in=wh_map.keys(),
        ).values('transfer__destination_warehouse_id').annotate(
            qty=_Sum(_F('sent_qty') - _F('received_qty')),
        )
        transit_by_wh = {r['transfer__destination_warehouse_id']: r['qty'] or Decimal('0') for r in in_transit_qs}

        for wh_id, wh in wh_map.items():
            stocks = Stock.objects.filter(warehouse_id=wh_id)
            total_qty = Decimal('0')
            total_val = Decimal('0')
            for s in stocks.iterator():
                net = s.quantity - s.oversold_quantity
                total_qty += net
                total_val += q_total_cost(net * (s.average_cost or Decimal('0')))
            rows.append({
                'warehouse_id': wh_id,
                'warehouse_name': wh.name,
                'total_qty': str(q_qty(total_qty)),
                'total_valuation': str(q_total_cost(total_val)),
                'in_transit_qty': str(q_qty(transit_by_wh.get(wh_id, Decimal('0')))),
            })
        return {'series_type': 'current', 'as_of': None, 'warehouses': rows}

    # Historical point-in-time valuation as of date
    for wh_id, wh in wh_map.items():
        mvs = StockMovement.objects.filter(warehouse_id=wh_id, movement_date__lte=as_of).order_by('product_id', 'posted_at', 'pk')
        last_by_prod = {}
        for mv in mvs.iterator():
            last_by_prod[mv.product_id] = mv
        total_qty = Decimal('0')
        total_val = Decimal('0')
        for mv in last_by_prod.values():
            total_qty += mv.balance_after
            total_val += q_total_cost(mv.balance_after * (mv.unit_cost_base or Decimal('0')))
        rows.append({
            'warehouse_id': wh_id,
            'warehouse_name': wh.name,
            'total_qty': str(q_qty(total_qty)),
            'total_valuation': str(q_total_cost(total_val)),
            'in_transit_qty': '0',
        })
    return {'series_type': 'historical', 'as_of': str(as_of), 'warehouses': rows}
