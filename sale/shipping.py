"""Courier, Delivery, Packing Slip, and Challan services (SHIP-01; zero stock/cash side effects)."""

from decimal import Decimal
from urllib.parse import quote

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.exceptions import StateConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog
from common.money import q_qty
from common.settings_store import get_general
from warehouse.scope import check_warehouse, lock_warehouse_scope, scoped_queryset
from .models import (
    Challan, ChallanItem, Courier, Delivery, DeliveryItem,
    PackingSlip, PackingSlipItem, SalesOrder, SalesReturnItem,
)


ZERO = Decimal('0')


def _net_deliverable_qty(line) -> Decimal:
    returned = SalesReturnItem.objects.filter(
        sales_order_item=line, sales_return__is_completed=True,
    ).aggregate(s=Sum('quantity'))['s'] or ZERO
    return max(Decimal(line.quantity) - returned, ZERO)


def create_delivery(actor, sales_order_id: int, lines: list[dict], *, courier_id=None, address='', note='', delivered_by='') -> Delivery:
    if not has_role_permissions(actor, ('sale.add_delivery',)):
        raise PermissionDenied('You do not have permission to create deliveries.')
    if not lines:
        raise ValidationError({'items': 'Add at least one delivery line.'})

    with transaction.atomic():
        scope = lock_warehouse_scope(actor)
        order = scoped_queryset(SalesOrder.objects.select_for_update(), scope).filter(pk=sales_order_id, is_completed=True).first()
        if order is None:
            raise NotFound('Completed sales order not found.')
        check_warehouse(scope, order.warehouse_id)

        courier = None
        if courier_id:
            courier = Courier.objects.filter(pk=courier_id, is_active=True).first()
            if courier is None:
                raise ValidationError({'courier': 'Choose an active courier.'})

        by_item = {it.pk: it for it in order.items.select_for_update()}
        username = actor.get_username()
        snap_addr = address or order.shipping_address or (order.customer.address if order.customer else '')

        deliv = Delivery.objects.create(
            sales_order=order, courier=courier, delivered_by=delivered_by,
            customer=order.customer, address=snap_addr, note=note, created_by=actor,
            create_by=username, modified_by=username,
        )
        for entry in lines:
            lid = entry.get('sales_order_item')
            line = by_item.get(lid)
            if line is None:
                raise ValidationError({'items': f'Sales order item #{lid} does not belong to this order.'})
            qty = q_qty(entry.get('quantity') or 0)
            if qty <= ZERO:
                raise ValidationError({'items': 'Delivery quantity must be positive.'})
            already = DeliveryItem.objects.filter(sales_order_item=line).aggregate(s=Sum('quantity'))['s'] or ZERO
            avail = _net_deliverable_qty(line) - already
            if qty > avail:
                raise ValidationError({'items': f'Line {line.line_number}: delivery qty {qty} exceeds remaining {avail}.'})
            DeliveryItem.objects.create(delivery=deliv, sales_order_item=line, quantity=qty, create_by=username, modified_by=username)

        log_audit_event(actor, AuditLog.Action.CREATE, deliv, changes={'sales_order_id': order.pk}, warehouse=order.warehouse)
        return deliv


def update_delivery_status(actor, delivery_id: int, status: str, *, received_by: str = '') -> Delivery:
    if not has_role_permissions(actor, ('sale.change_delivery',)):
        raise PermissionDenied('You do not have permission to update deliveries.')
    if status not in Delivery.Status.values:
        raise ValidationError({'status': 'Invalid delivery status.'})
    with transaction.atomic():
        scope = lock_warehouse_scope(actor)
        deliv = scoped_queryset(
            Delivery.objects.select_for_update(), scope, lookup='sales_order__warehouse_id',
        ).filter(pk=delivery_id).first()
        if deliv is None:
            raise NotFound('Delivery not found.')
        deliv.status = status
        if received_by:
            deliv.received_by = received_by
        deliv.modified_by = actor.get_username()
        deliv.save(update_fields=['status', 'received_by', 'modified_by', 'updated_at'])
        return deliv


def create_packing_slip(actor, sales_order_id: int, lines: list[dict], *, delivery_id=None) -> PackingSlip:
    if not get_general().enable_packing_slip:
        raise ValidationError({'enable_packing_slip': 'Packing slips are disabled in general settings.'})
    if not has_role_permissions(actor, ('sale.manage_packing_slip',)):
        raise PermissionDenied('You do not have permission to manage packing slips.')
    if not lines:
        raise ValidationError({'items': 'Add at least one item to the packing slip.'})

    with transaction.atomic():
        scope = lock_warehouse_scope(actor)
        order = scoped_queryset(SalesOrder.objects.select_for_update(), scope).filter(pk=sales_order_id, is_completed=True).first()
        if order is None:
            raise NotFound('Completed sales order not found.')
        by_item = {it.pk: it for it in order.items.select_for_update()}
        username = actor.get_username()
        slip = PackingSlip.objects.create(
            sales_order=order, delivery_id=delivery_id, status=PackingSlip.Status.OPEN,
            create_by=username, modified_by=username,
        )
        for entry in lines:
            lid = entry.get('sales_order_item')
            line = by_item.get(lid)
            if line is None:
                raise ValidationError({'items': f'Sales order item #{lid} not in this order.'})
            qty = q_qty(entry.get('quantity') or 0)
            if qty <= ZERO:
                raise ValidationError({'items': 'Packing slip quantity must be positive.'})
            already = PackingSlipItem.objects.filter(sales_order_item=line).aggregate(s=Sum('quantity'))['s'] or ZERO
            avail = _net_deliverable_qty(line) - already
            if qty > avail:
                raise ValidationError({'items': f'Line {line.line_number}: packed qty {qty} exceeds remaining {avail}.'})
            PackingSlipItem.objects.create(packing_slip=slip, sales_order_item=line, quantity=qty, create_by=username, modified_by=username)
        return slip


def create_challan(actor, slip_ids: list[int], courier_id: int, *, amount=ZERO) -> Challan:
    if not has_role_permissions(actor, ('sale.issue_challan',)):
        raise PermissionDenied('You do not have permission to issue challans.')
    if not slip_ids:
        raise ValidationError({'packing_slips': 'Select at least one packing slip.'})

    with transaction.atomic():
        scope = lock_warehouse_scope(actor)
        courier = Courier.objects.filter(pk=courier_id, is_active=True).first()
        if courier is None:
            raise ValidationError({'courier': 'Choose an active courier.'})
        slips = list(
            scoped_queryset(
                PackingSlip.objects.select_for_update(), scope, lookup='sales_order__warehouse_id',
            ).filter(pk__in=slip_ids)
        )
        if len(slips) != len(set(slip_ids)):
            raise NotFound('One or more packing slips were not found.')
        username = actor.get_username()
        challan = Challan.objects.create(
            courier=courier, status=Challan.Status.OPEN, amount=amount or ZERO,
            created_by=actor, create_by=username, modified_by=username,
        )
        for slip in slips:
            if slip.status == PackingSlip.Status.CLOSED:
                raise ValidationError({'packing_slips': f'Packing slip {slip.reference_number} is already closed.'})
            try:
                ChallanItem.objects.create(
                    challan=challan, packing_slip=slip, sales_order=slip.sales_order, is_open=True,
                    create_by=username, modified_by=username,
                )
            except IntegrityError as exc:
                raise StateConflict(f'Packing slip {slip.reference_number} is already on an open challan.') from exc
            slip.status = PackingSlip.Status.ON_CHALLAN
            slip.modified_by = username
            slip.save(update_fields=['status', 'modified_by', 'updated_at'])
        return challan


def dispatch_challan(actor, challan_id: int) -> Challan:
    if not has_role_permissions(actor, ('sale.issue_challan',)):
        raise PermissionDenied('You do not have permission to dispatch challans.')
    with transaction.atomic():
        challan = Challan.objects.select_for_update().filter(pk=challan_id).first()
        if challan is None:
            raise NotFound('Challan not found.')
        if challan.status != Challan.Status.OPEN:
            raise StateConflict('Only open challans can be dispatched.')
        challan.status = Challan.Status.DISPATCHED
        challan.modified_by = actor.get_username()
        challan.save(update_fields=['status', 'modified_by', 'updated_at'])
        return challan


def close_challan(actor, challan_id: int, request_key, *, closing_date=None) -> tuple[Challan, bool]:
    if not has_role_permissions(actor, ('sale.close_challan',)):
        raise PermissionDenied('You do not have permission to close challans.')

    def _do():
        with transaction.atomic():
            Challan.objects.filter(pk=challan_id).update(updated_at=F('updated_at'))
            challan = Challan.objects.select_for_update().filter(pk=challan_id).first()
            if challan is None:
                raise NotFound('Challan not found.')
            if challan.status == Challan.Status.CLOSED:
                return IdempotentResult('challan_close', challan.pk, {'id': challan.pk}, value=challan)
            now = timezone.now()
            username = actor.get_username()
            challan.status = Challan.Status.CLOSED
            challan.closed_by = actor
            challan.closed_at = now
            challan.closing_date = closing_date or timezone.localdate(now)
            challan.modified_by = username
            challan.save(update_fields=['status', 'closed_by', 'closed_at', 'closing_date', 'modified_by', 'updated_at'])
            ChallanItem.objects.filter(challan=challan).update(is_open=False, modified_by=username, updated_at=now)
            PackingSlip.objects.filter(challan_links__challan=challan).update(
                status=PackingSlip.Status.CLOSED, modified_by=username, updated_at=now,
            )
            log_audit_event(actor, AuditLog.Action.CLOSE, challan, changes={'closing_date': str(challan.closing_date)}, request_key=request_key)
            return IdempotentResult('challan_close', challan.pk, {'id': challan.pk}, value=challan)

    return run_idempotent(
        request_key, f'close_challan:{challan_id}', {'challan_id': challan_id}, _do,
        loader=lambda r: Challan.objects.get(pk=r.result_id),
    )


def whatsapp_link(order: SalesOrder) -> str:
    phone = ''.join(ch for ch in (getattr(order.customer, 'phone', '') or '') if ch.isdigit())
    msg = f'Invoice {order.order_number} Total {order.currency_id} {order.grand_total}'
    return f'https://wa.me/{phone}?text={quote(msg)}'
