"""Receive complete or partial purchases atomically and protect received purchase writes."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from uuid import uuid4

from django.db import IntegrityError, OperationalError, connections, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.models import PaymentOrigin
from accounting.services import post_purchase_payment, post_received_purchase_payment, refresh_payment_status
from common.audit import log_audit_event
from common.exceptions import IdempotencyConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog, SettlementMode
from common.money import convert_to_base, q_cost, q_qty
from common.pricing import calculate_document_totals
from inventory.models import Product, require_convertible_unit
from warehouse.models import Stock, StockMovement, StockSerial, WareHouse
from warehouse.tracking import receive_tracking
from warehouse.scope import lock_warehouse_scope, scoped_queryset
from warehouse.services import MAX_STOCK_QUANTITY, post_stock_movement
from .models import PurchaseOrder, PurchaseOrderItem, PurchaseReceipt, PurchaseReceiptItem


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
    if order.is_received or order.receipt_state in (PurchaseOrder.ReceiptState.PARTIAL, PurchaseOrder.ReceiptState.RECEIVED):
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


def _has_receive_permission(actor):
    return (
        has_role_permissions(actor, ('purchase.change_purchaseorder',))
        or has_role_permissions(actor, ('purchase.receive_purchase',))
    )


def _freeze_order_pricing(order, items):
    totals = calculate_document_totals(
        [
            {
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'discount_amount': item.discount_amount,
                'tax_rate': item.tax_rate,
                'tax_method': item.tax_method,
            }
            for item in items
        ],
        order_discount_type=order.order_discount_type,
        order_discount_value=order.order_discount_value,
        order_tax_rate=order.order_tax_rate,
        shipping_cost=order.shipping_cost,
        digits=4,
    )
    for item, priced in zip(items, totals.lines):
        item.allocated_order_discount = priced.allocated_order_discount
        item.allocated_order_tax = priced.allocated_order_tax
        item.save(update_fields=['allocated_order_discount', 'allocated_order_tax', 'updated_at'])
    order.order_discount_amount = totals.order_discount_amount
    order.order_tax_amount = totals.order_tax_amount


def _compute_recognized_base(order, items):
    with localcontext() as ctx:
        ctx.prec = 50
        rec_doc = Decimal('0.0000')
        any_received = False
        for item in items:
            if item.received_quantity > 0:
                any_received = True
                ratio = item.received_quantity / item.quantity
                rec_doc += (item.returnable_total * ratio).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        if any_received:
            rec_doc += Decimal(order.shipping_cost or 0)
        return convert_to_base(rec_doc, order.exchange_rate, digits=4)


def receive_purchase(
    actor,
    purchase_id,
    *,
    receipts=None,
    receipt_date=None,
    request_key=None,
    settlement_mode=None,
    tenders=None,
    payment_date=None,
    notes='',
):
    if not _has_receive_permission(actor):
        raise PermissionDenied('You do not have permission to receive a purchase.')

    is_partial_api = receipts is not None
    if is_partial_api and not receipts:
        raise ValidationError({'lines': 'Provide at least one receipt line.'})

    if is_partial_api and request_key is not None:
        payload = {
            'purchase_id': purchase_id,
            'receipt_date': str(receipt_date or timezone.localdate()),
            'lines': receipts,
            'settlement_mode': settlement_mode,
            'tenders': tenders or [],
        }

        def _run():
            order, created_now, receipt = _receive_purchase_inner(
                actor, purchase_id, receipts=receipts, receipt_date=receipt_date,
                request_key=request_key, settlement_mode=settlement_mode,
                tenders=tenders, payment_date=payment_date, notes=notes,
            )
            remaining = [
                {
                    'order_line': item.pk,
                    'ordered': format(item.quantity, '.6f'),
                    'received': format(item.received_quantity, '.6f'),
                    'remaining': format(item.quantity - item.received_quantity, '.6f'),
                }
                for item in order.items.order_by('line_number', 'pk')
            ]
            data = {
                'receipt_id': receipt.pk,
                'receipt_number': receipt.receipt_number,
                'remaining': remaining,
                'receipt_state': order.receipt_state,
                'payment_status': order.payment_status,
            }
            return IdempotentResult('purchase_receipt', receipt.pk, data, data)

        try:
            res, _ = run_idempotent(request_key, 'purchase_receipt', payload, _run)
            return res
        except OperationalError as exc:
            if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
                raise PurchaseBusy() from exc
            raise

    order, done, _ = _receive_purchase_inner(
        actor, purchase_id, receipts=receipts, receipt_date=receipt_date,
        request_key=request_key, settlement_mode=settlement_mode,
        tenders=tenders, payment_date=payment_date, notes=notes,
    )
    return order, done


def _receive_purchase_inner(
    actor,
    purchase_id,
    *,
    receipts=None,
    receipt_date=None,
    request_key=None,
    settlement_mode=None,
    tenders=None,
    payment_date=None,
    notes='',
):
    try:
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            if not _has_receive_permission(actor):
                raise PermissionDenied('Your role does not have permission to receive a purchase.')
            orders = scoped_queryset(PurchaseOrder.objects.all(), scope)
            now = timezone.now()
            is_legacy_full = receipts is None

            if is_legacy_full:
                # Legacy once-only atomic claim
                claimed = orders.filter(pk=purchase_id, is_received=False).update(
                    is_received=True, modified_by=actor.get_username(), updated_at=now,
                )
                try:
                    order = orders.select_for_update().get(pk=purchase_id)
                except PurchaseOrder.DoesNotExist as exc:
                    raise NotFound('Purchase order not found.') from exc
                if not claimed:
                    return order, False, None
            else:
                if not connections[PurchaseOrder.objects.db].features.has_select_for_update:
                    orders.filter(pk=purchase_id).update(updated_at=now)
                order = orders.select_for_update().filter(pk=purchase_id).first()
                if order is None:
                    raise NotFound('Purchase order not found.')
                if order.is_received:
                    raise ValidationError({'purchase_order': 'This purchase order has already been fully received.'})

            if not order.warehouse_id:
                raise ValidationError({'warehouse': 'Set the receiving warehouse on this purchase before receiving it.'})
            warehouse = WareHouse.objects.select_for_update().filter(pk=order.warehouse_id).first()
            if warehouse is None or not warehouse.is_active:
                raise ValidationError({'warehouse': 'Choose an active receiving warehouse.'})
            items = list(order.items.order_by('line_number', 'pk'))
            if not items:
                raise ValidationError({'items': 'Add at least one purchase item before receiving.'})
            items_by_id = {item.pk: item for item in items}
            products = {product.pk: product for product in Product.objects.select_for_update().filter(
                pk__in={item.product_id for item in items}).order_by('pk')}

            # Build per-line receipt quantities (validating convertible units BEFORE mutating pricing fields)
            rec_lines = []
            if is_legacy_full:
                for item in items:
                    product = products.get(item.product_id)
                    if product is None:
                        raise ValidationError({'items': f'Line {item.line_number}: product missing.'})
                    require_convertible_unit(item.unit, product.unit, field='items')
                    if item.quantity <= 0:
                        raise ValidationError({'items': 'Purchase quantities must be positive.'})
                    rem = item.quantity - item.received_quantity
                    if rem > 0:
                        rec_lines.append((item, rem, '', None, []))
            else:
                seen_lines = set()
                for entry in receipts:
                    line_id = entry.get('order_line')
                    if line_id in seen_lines:
                        raise ValidationError({'lines': 'Each purchase order line may appear only once per receipt.'})
                    seen_lines.add(line_id)
                    item = items_by_id.get(line_id)
                    if item is None:
                        raise ValidationError({'lines': f'Line {line_id} does not belong to this purchase order.'})
                    product = products.get(item.product_id)
                    if product is None:
                        raise ValidationError({'lines': f'Line {item.line_number}: product missing.'})
                    require_convertible_unit(item.unit, product.unit, field='items')
                    try:
                        qty = Decimal(str(entry.get('quantity', '0')))
                    except Exception as exc:
                        raise ValidationError({'lines': 'Invalid receipt quantity.'}) from exc
                    if qty <= 0:
                        raise ValidationError({'lines': f'Line {item.line_number}: receipt quantity must be positive.'})
                    remaining = item.quantity - item.received_quantity
                    if qty > remaining:
                        raise ValidationError({'lines': f'Line {item.line_number}: quantity {qty} exceeds remaining {remaining}.'})
                    rec_lines.append((
                        item, qty, str(entry.get('batch_number') or ''),
                        entry.get('expiry_date'), list(entry.get('serials') or []),
                    ))

            if not order.receipts.exists():
                _freeze_order_pricing(order, items)

            if not rec_lines:
                raise ValidationError({'lines': 'No outstanding quantities remain to receive.'})

            rec_date = receipt_date or order.order_date
            receipt = PurchaseReceipt.objects.create(
                purchase_order=order,
                warehouse=warehouse,
                receipt_date=rec_date,
                received_by=actor,
                notes=notes,
                request_key=request_key or uuid4(),
                create_by=actor.get_username(),
                modified_by=actor.get_username(),
            )

            for item, qty, batch_no, exp_date, serials in sorted(rec_lines, key=lambda row: (row[0].product_id, row[0].line_number, row[0].pk)):
                if qty > MAX_STOCK_QUANTITY:
                    raise ValidationError({'quantity': 'The received quantity exceeds the supported stock limit.'})
                product = products[item.product_id]
                factor = require_convertible_unit(item.unit, product.unit, field='items')
                base_qty = q_qty(qty * factor)
                if item.unit_conversion_factor != factor or item.base_quantity != q_qty(item.quantity * factor):
                    item.unit_conversion_factor = factor
                    item.base_quantity = q_qty(item.quantity * factor)
                    item.save(update_fields=['unit_conversion_factor', 'base_quantity', 'updated_at'])
                eff_unit_doc = (item.returnable_total / item.quantity) if item.quantity else item.unit_price
                line_unit_cost_base = q_cost(eff_unit_doc * order.exchange_rate / factor)
                if product.product_type_id in ('service', 'digital'):
                    PurchaseReceiptItem.objects.create(
                        receipt=receipt,
                        purchase_order_item=item,
                        unit_id=item.unit_id,
                        quantity=qty,
                        unit_conversion_factor=factor,
                        base_quantity=base_qty,
                        batch_number=batch_no,
                        expiry_date=exp_date,
                        unit_cost=eff_unit_doc.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP),
                        unit_cost_base=line_unit_cost_base,
                        create_by=actor.get_username(),
                        modified_by=actor.get_username(),
                    )
                    item.received_quantity += qty
                    item.unit_cost_base = line_unit_cost_base
                    item.save(update_fields=['received_quantity', 'unit_cost_base', 'updated_at'])
                    continue

                batch_obj, serial_objs = receive_tracking(
                    product, warehouse.pk, base_qty,
                    batch_number=batch_no, expiry_date=exp_date,
                    unit_cost=line_unit_cost_base, serials=serials, actor=actor,
                )
                r_item = PurchaseReceiptItem.objects.create(
                    receipt=receipt,
                    purchase_order_item=item,
                    unit_id=item.unit_id,
                    quantity=qty,
                    unit_conversion_factor=factor,
                    base_quantity=base_qty,
                    batch=batch_obj,
                    batch_number=batch_no,
                    expiry_date=exp_date,
                    serial_numbers=[s.serial_number for s in serial_objs],
                    unit_cost=eff_unit_doc.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP),
                    unit_cost_base=line_unit_cost_base,
                    create_by=actor.get_username(),
                    modified_by=actor.get_username(),
                )
                move = post_stock_movement(
                    warehouse_id=warehouse.pk,
                    product_id=item.product_id,
                    kind=StockMovement.Kind.PURCHASE_RECEIPT,
                    quantity_delta=base_qty,
                    unit_cost_base=line_unit_cost_base,
                    batch_id=batch_obj.pk if batch_obj else None,
                    serial_ids=[s.pk for s in serial_objs],
                    serial_status=StockSerial.Status.IN_STOCK,
                    movement_date=rec_date,
                    reference_type='purchase_receipt',
                    reference_id=receipt.pk,
                    reference_line_id=r_item.pk,
                    posting_key=f'purchase_receipt:{receipt.pk}:{r_item.pk}',
                    actor=actor,
                )
                item.received_quantity += qty
                item.unit_cost_base = move.unit_cost_base
                if batch_obj and item.batch_id is None:
                    item.batch = batch_obj
                item.save(update_fields=['received_quantity', 'unit_cost_base', 'batch', 'updated_at'])

            all_done = all(item.received_quantity >= item.quantity for item in items)
            order.receipt_state = (
                PurchaseOrder.ReceiptState.RECEIVED if all_done else PurchaseOrder.ReceiptState.PARTIAL
            )
            order.is_received = all_done
            mode = settlement_mode or order.settlement_mode or SettlementMode.AUTO_FULL
            if mode not in SettlementMode.values:
                raise ValidationError({'settlement_mode': 'Choose auto_full, deferred, or explicit.'})
            order.settlement_mode = mode
            order.recognized_total_base = _compute_recognized_base(order, items)
            order.modified_by = actor.get_username()
            order.save(update_fields=[
                'order_discount_amount', 'order_tax_amount', 'receipt_state', 'is_received',
                'settlement_mode', 'recognized_total_base', 'modified_by', 'updated_at',
            ])

            local_date = payment_date or timezone.localdate(now)
            if mode == SettlementMode.AUTO_FULL:
                post_received_purchase_payment(order, scope, actor, date=local_date)
            elif mode == SettlementMode.EXPLICIT and tenders:
                post_purchase_payment(
                    actor, order.pk, tenders, local_date, uuid4(),
                    origin=PaymentOrigin.MANUAL, bypass_perm_check=True,
                )
            refresh_payment_status(order)
            log_audit_event(
                actor, AuditLog.Action.RECEIVE, instance=order,
                changes={'receipt_id': receipt.pk, 'receipt_state': order.receipt_state, 'payment_status': order.payment_status},
                module=AuditLog.Module.PURCHASE, request_key=request_key, warehouse=warehouse,
            )
            return order, True, receipt
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
            raise PurchaseBusy() from exc
        raise
    except IntegrityError as exc:
        raise PurchaseBusy('The purchase could not be received. Check the stock references and retry.') from exc
    except InvalidOperation as exc:
        raise ValidationError({'quantity': 'The stock quantity exceeds the database precision. The purchase was not received.'}) from exc


@transaction.atomic
def duplicate_purchase_order(actor, order_id):
    if not has_role_permissions(actor, ('purchase.view_purchaseorder', 'purchase.add_purchaseorder')):
        raise PermissionDenied('You do not have permission to duplicate purchase orders.')
    scope = lock_warehouse_scope(actor)
    source = scoped_queryset(PurchaseOrder.objects.prefetch_related('items'), scope).filter(pk=order_id).first()
    if source is None:
        raise NotFound('Purchase order not found.')
    username = actor.get_username()
    clone = PurchaseOrder.objects.create(
        supplier=source.supplier,
        warehouse=source.warehouse,
        payment_account=source.payment_account,
        settlement_mode=source.settlement_mode,
        order_date=timezone.localdate(),
        expected_date=None,
        currency=source.currency,
        exchange_rate=source.exchange_rate,
        status_id='draft',
        supplier_reference=source.supplier_reference,
        shipping_cost=source.shipping_cost,
        order_discount_type=source.order_discount_type,
        order_discount_value=source.order_discount_value,
        order_tax_rule=source.order_tax_rule,
        order_tax_rate=source.order_tax_rate,
        staff_note=source.staff_note,
        notes=source.notes,
        create_by=username,
        modified_by=username,
    )
    for line in source.items.all():
        PurchaseOrderItem.objects.create(
            purchase_order=clone,
            line_number=line.line_number,
            product=line.product,
            description=line.description,
            unit=line.unit,
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount_amount=line.discount_amount,
            tax_rate=line.tax_rate,
            tax_method=line.tax_method,
            create_by=username,
            modified_by=username,
        )
    log_audit_event(actor, AuditLog.Action.CREATE, instance=clone, changes={'duplicated_from': source.pk}, module=AuditLog.Module.PURCHASE)
    return clone
