"""Atomic stock adjustment posting service."""

from decimal import Decimal

from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.exceptions import StateConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog
from common.money import q_cost, q_qty
from inventory.models import Product, require_convertible_unit
from .models import StockAdjustment, StockMovement, WareHouse
from .scope import lock_warehouse_scope, scoped_queryset
from .services import TransferBusy, post_stock_movement
from .tracking import allocate_tracking


def _post_adjustment_inner(actor, adjustment_id: int, request_key, *, bypass_perm: bool = False):
    scope = lock_warehouse_scope(actor)
    if not bypass_perm and not (
        has_role_permissions(actor, ('warehouse.post_adjustment',))
        or has_role_permissions(actor, ('warehouse.change_stockadjustment',))
    ):
        raise PermissionDenied('You do not have permission to post stock adjustments.')

    qs = scoped_queryset(StockAdjustment.objects.all(), scope)
    qs.filter(pk=adjustment_id).update(updated_at=F('updated_at'))
    adj = qs.select_for_update().filter(pk=adjustment_id).first()
    if adj is None:
        raise NotFound('Stock adjustment not found.')
    if adj.status == StockAdjustment.Status.POSTED:
        if adj.request_key and str(adj.request_key) == str(request_key):
            return adj, False
        raise StateConflict('This stock adjustment has already been posted.')

    wh = WareHouse.objects.select_for_update().filter(pk=adj.warehouse_id, is_active=True).first()
    if wh is None:
        raise ValidationError({'warehouse': 'Choose an active warehouse.'})

    items = list(adj.items.select_related('product', 'unit', 'batch').order_by('product_id', 'pk'))
    if not items:
        raise ValidationError({'items': 'Add at least one adjustment line before posting.'})

    products = {
        p.pk: p
        for p in Product.objects.select_for_update().filter(pk__in={it.product_id for it in items}).order_by('pk')
    }
    now = timezone.now()
    for item in items:
        product = products[item.product_id]
        if product.product_type_id in ('service', 'digital', 'combo'):
            raise ValidationError({'items': f'{product.sku} cannot be stock-adjusted directly.'})
        factor = require_convertible_unit(item.unit, product.unit, field='items')
        base_qty = q_qty(item.quantity * factor)
        if item.unit_conversion_factor != factor or item.base_quantity != base_qty:
            item.unit_conversion_factor = factor
            item.base_quantity = base_qty
            item.save(update_fields=['unit_conversion_factor', 'base_quantity', 'updated_at'])

        if item.action == item.Action.ADDITION:
            if item.unit_cost is None:
                raise ValidationError({'unit_cost': f'Unit cost is required for adding {product.sku}.'})
            cost_base = q_cost(Decimal(item.unit_cost) / factor)
            post_stock_movement(
                warehouse_id=wh.pk,
                product_id=product.pk,
                kind=StockMovement.Kind.ADJUSTMENT_ADD,
                quantity_delta=base_qty,
                unit_cost_base=cost_base,
                batch_id=item.batch_id,
                movement_date=adj.adjustment_date,
                reference_type='stock_adjustment',
                reference_id=adj.pk,
                reference_line_id=item.pk,
                posting_key=f'stock_adjustment:{adj.pk}:{item.pk}',
                notes=adj.reason or adj.note,
                actor=actor,
            )
        else:
            batch_obj, _ = allocate_tracking(
                product, wh.pk, base_qty, batch_id=item.batch_id, doc_date=adj.adjustment_date,
            ) if (product.has_batch_expiry or item.batch_id) else (None, [])
            post_stock_movement(
                warehouse_id=wh.pk,
                product_id=product.pk,
                kind=StockMovement.Kind.ADJUSTMENT_SUB,
                quantity_delta=-base_qty,
                unit_cost_base=None,
                batch_id=batch_obj.pk if batch_obj else item.batch_id,
                movement_date=adj.adjustment_date,
                reference_type='stock_adjustment',
                reference_id=adj.pk,
                reference_line_id=item.pk,
                posting_key=f'stock_adjustment:{adj.pk}:{item.pk}',
                notes=adj.reason or adj.note,
                actor=actor,
            )

    adj.status = StockAdjustment.Status.POSTED
    adj.request_key = request_key
    adj.posted_at = now
    adj.modified_by = actor.get_username()
    adj.save(update_fields=['status', 'request_key', 'posted_at', 'modified_by', 'updated_at'])
    log_audit_event(
        actor, AuditLog.Action.POST, adj,
        changes={'status': adj.status, 'reason': adj.reason, 'line_count': len(items)},
        request_key=request_key, warehouse=wh,
    )
    return adj, True


def post_stock_adjustment(actor, adjustment_id: int, request_key, *, bypass_perm: bool = False):
    if not bypass_perm and not (
        has_role_permissions(actor, ('warehouse.post_adjustment',))
        or has_role_permissions(actor, ('warehouse.change_stockadjustment',))
    ):
        raise PermissionDenied('You do not have permission to post stock adjustments.')

    scope = f'stock_adjustment:{adjustment_id}'

    def _loader(rec):
        return StockAdjustment.objects.get(pk=rec.result_id)

    def _do():
        try:
            with transaction.atomic():
                adj, _ = _post_adjustment_inner(actor, adjustment_id, request_key, bypass_perm=bypass_perm)
                return IdempotentResult(
                    result_type='stock_adjustment', result_id=adj.pk,
                    result_data={'id': adj.pk, 'status': adj.status, 'reference_number': adj.reference_number},
                    value=adj,
                )
        except OperationalError as exc:
            if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
                raise TransferBusy() from exc
            raise

    adj, created = run_idempotent(request_key, scope, {'adjustment_id': adjustment_id}, _do, loader=_loader)
    return adj, created
