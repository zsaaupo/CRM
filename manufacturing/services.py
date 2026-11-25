"""Raw material movements, BOM approval, production preview & atomic posting (D-41, D-42, D-43)."""

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext
from uuid import uuid4

from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.exceptions import InsufficientStock, StateConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog
from common.money import q_cost, q_qty, q_total_cost
from common.settings_store import get_general
from inventory.models import require_convertible_unit
from warehouse.models import StockMovement, WareHouse
from warehouse.scope import check_warehouse, lock_warehouse_scope, scoped_queryset
from warehouse.services import TransferBusy, post_stock_movement
from .models import (
    BOMComponent, BillOfMaterials, ProductionConsumptionLine, ProductionOrder,
    RawMaterial, RawMaterialMovement, RawMaterialStock,
)


ZERO = Decimal('0')


def compute_component_required_qty(component: BOMComponent, bom_base_output: Decimal, planned_qty: Decimal) -> Decimal:
    """D-41 wastage & issue_multiple rounding formula."""
    with localcontext() as ctx:
        ctx.prec = 50
        factor = require_convertible_unit(component.unit, component.raw_material.stock_unit, field='components')
        raw = Decimal(planned_qty) / Decimal(bom_base_output) * Decimal(component.quantity_per_base_output) * factor
        waste = Decimal(component.wastage_percent or 0)
        if waste > ZERO:
            if component.wastage_basis == BOMComponent.WastageBasis.GROSS:
                req = raw / (Decimal('1') - waste / Decimal('100'))
            else:
                req = raw * (Decimal('1') + waste / Decimal('100'))
        else:
            req = raw
        step = component.issue_multiple
        if step is not None and Decimal(step) > ZERO:
            step_d = Decimal(step)
            units = (req / step_d).to_integral_value(rounding=ROUND_CEILING)
            req = units * step_d
        return req.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)


def receive_raw_material(actor, material_id: int, warehouse_id: int, qty, unit_cost, kind: str, request_key):
    if not has_role_permissions(actor, ('manufacturing.manage_raw_material',)):
        raise PermissionDenied('You do not have permission to manage raw material stock.')
    q = q_qty(qty)
    cost = q_cost(unit_cost)
    if q <= ZERO:
        raise ValidationError({'quantity': 'Quantity must be positive.'})
    if kind not in (RawMaterialMovement.Kind.OPENING, RawMaterialMovement.Kind.REPLENISH,
                    RawMaterialMovement.Kind.ADJUST_ADD, RawMaterialMovement.Kind.ADJUST_SUB):
        raise ValidationError({'kind': 'Invalid raw material movement kind.'})

    delta = -q if kind == RawMaterialMovement.Kind.ADJUST_SUB else q

    def _do():
        with transaction.atomic(), localcontext() as ctx:
            ctx.prec = 50
            scope = lock_warehouse_scope(actor)
            wh = WareHouse.objects.select_for_update().filter(pk=warehouse_id, is_active=True).first()
            if wh is None:
                raise ValidationError({'warehouse': 'Choose an active warehouse.'})
            check_warehouse(scope, wh.pk)

            mat = RawMaterial.objects.select_for_update().get(pk=material_id)
            RawMaterialStock.objects.filter(warehouse=wh, raw_material=mat).update(updated_at=F('updated_at'))
            rms, _ = RawMaterialStock.objects.select_for_update().get_or_create(
                warehouse=wh, raw_material=mat,
                defaults={'quantity': ZERO, 'average_cost': cost, 'create_by': actor.get_username(), 'modified_by': actor.get_username()},
            )
            old_qty = Decimal(rms.quantity)
            old_avg = Decimal(rms.average_cost or ZERO)
            new_qty = old_qty + delta
            if new_qty < ZERO:
                raise InsufficientStock({'quantity': f'Insufficient stock for raw material {mat.code}.'})

            if delta > ZERO:
                if old_qty <= ZERO:
                    new_avg = cost
                else:
                    new_avg = q_cost((old_qty * old_avg + q * cost) / new_qty)
                applied_cost = cost
            else:
                new_avg = old_avg
                applied_cost = old_avg

            rms.quantity = new_qty
            rms.average_cost = new_avg
            rms.modified_by = actor.get_username()
            rms.save(update_fields=['quantity', 'average_cost', 'modified_by', 'updated_at'])
            mat.current_unit_cost = new_avg
            mat.save(update_fields=['current_unit_cost', 'updated_at'])

            mv = RawMaterialMovement.objects.create(
                warehouse=wh, raw_material=mat, kind=kind,
                quantity_delta=delta, unit_cost=applied_cost, balance_after=new_qty,
                reference_type='raw_material_receipt', reference_id=mat.pk,
                posting_key=f'rm:{request_key}:{mat.pk}:{wh.pk}',
                create_by=actor.get_username(), modified_by=actor.get_username(),
            )
            log_audit_event(
                actor, AuditLog.Action.RECEIVE, mv,
                changes={'material': mat.code, 'delta': str(delta), 'avg_cost': str(new_avg)},
                request_key=request_key, warehouse=wh,
            )
            return IdempotentResult('raw_material_movement', mv.pk, {'id': mv.pk, 'balance': str(new_qty)}, value=mv)

    return run_idempotent(
        request_key, f'rm_receive:{material_id}:{warehouse_id}',
        {'material_id': material_id, 'warehouse_id': warehouse_id, 'qty': str(q), 'kind': kind, 'cost': str(cost)},
        _do, loader=lambda r: RawMaterialMovement.objects.get(pk=r.result_id),
    )


def approve_bom(actor, bom_id: int) -> BillOfMaterials:
    if not has_role_permissions(actor, ('manufacturing.approve_bom',)):
        raise PermissionDenied('You do not have permission to approve BOMs.')
    with transaction.atomic():
        bom = BillOfMaterials.objects.select_for_update().select_related('finished_product').filter(pk=bom_id).first()
        if bom is None:
            raise NotFound('BOM not found.')
        bom.clean()
        comps = list(bom.components.select_related('raw_material__stock_unit', 'unit'))
        if not comps:
            raise ValidationError({'components': 'Add at least one component before approving the BOM.'})
        for c in comps:
            require_convertible_unit(c.unit, c.raw_material.stock_unit, field='components')
        bom.status = BillOfMaterials.Status.APPROVED
        bom.approved_by = actor
        bom.approved_at = timezone.now()
        bom.modified_by = actor.get_username()
        bom.save(update_fields=['status', 'approved_by', 'approved_at', 'modified_by', 'updated_at'])
        return bom


def preview_production_requirements(bom_id: int, material_warehouse_id: int, planned_qty) -> list[dict]:
    bom = BillOfMaterials.objects.select_related('finished_product').prefetch_related(
        'components__raw_material__stock_unit', 'components__unit',
    ).filter(pk=bom_id).first()
    if bom is None:
        raise NotFound('BOM not found.')
    qty = q_qty(planned_qty)
    if qty <= ZERO:
        raise ValidationError({'planned_qty': 'Planned quantity must be positive.'})

    stocks = {
        s.raw_material_id: s
        for s in RawMaterialStock.objects.filter(warehouse_id=material_warehouse_id, raw_material_id__in=[c.raw_material_id for c in bom.components.all()])
    }
    rows = []
    for comp in bom.components.all():
        req = compute_component_required_qty(comp, bom.base_output_qty, qty)
        st = stocks.get(comp.raw_material_id)
        avail = Decimal(st.quantity) if st else ZERO
        unit_cost = Decimal(st.average_cost if st and st.average_cost else comp.raw_material.current_unit_cost)
        shortage = max(req - avail, ZERO)
        rows.append({
            'raw_material': comp.raw_material_id,
            'raw_material_code': comp.raw_material.code,
            'raw_material_name': comp.raw_material.name,
            'required': str(req),
            'available': str(q_qty(avail)),
            'shortage': str(q_qty(shortage)),
            'unit_cost': str(q_cost(unit_cost)),
            'est_cost': str(q_total_cost(req * unit_cost)),
        })
    return rows


def save_production_draft(actor, *, bom_id: int, material_warehouse_id: int, finished_warehouse_id: int,
                          planned_qty, accepted_good_qty=None, rejected_qty=0, labor_cost=0, overhead_cost=0,
                          production_date=None, notes='') -> ProductionOrder:
    if not get_general().enable_in_house_production:
        raise ValidationError({'enable_in_house_production': 'In-house production is disabled in general settings.'})
    if not (has_role_permissions(actor, ('manufacturing.add_productionorder',)) or has_role_permissions(actor, ('manufacturing.post_production',))):
        raise PermissionDenied('You do not have permission to create production orders.')

    with transaction.atomic():
        scope = lock_warehouse_scope(actor)
        check_warehouse(scope, material_warehouse_id)
        check_warehouse(scope, finished_warehouse_id)
        bom = BillOfMaterials.objects.select_related('finished_product').prefetch_related(
            'components__raw_material__stock_unit', 'components__unit',
        ).filter(pk=bom_id, status=BillOfMaterials.Status.APPROVED).first()
        if bom is None:
            raise ValidationError({'bom': 'Choose an approved BOM.'})
        if bom.finished_product.supply_method != 'in_house_production':
            raise ValidationError({'finished_product': 'Finished product does not use in-house production.'})

        p_qty = q_qty(planned_qty)
        acc = q_qty(accepted_good_qty if accepted_good_qty is not None else p_qty)
        rej = q_qty(rejected_qty or 0)
        username = actor.get_username()
        order = ProductionOrder.objects.create(
            bom=bom, finished_product=bom.finished_product,
            material_warehouse_id=material_warehouse_id, finished_warehouse_id=finished_warehouse_id,
            planned_qty=p_qty, accepted_good_qty=acc, rejected_qty=rej,
            labor_cost=q_total_cost(labor_cost or 0), overhead_cost=q_total_cost(overhead_cost or 0),
            production_date=production_date or timezone.localdate(),
            notes=notes or '', status=ProductionOrder.Status.DRAFT,
            create_by=username, modified_by=username,
        )
        preview = preview_production_requirements(bom.pk, material_warehouse_id, p_qty)
        for row in preview:
            ProductionConsumptionLine.objects.create(
                production_order=order,
                raw_material_id=row['raw_material'],
                planned_qty=Decimal(row['required']),
                available_qty_snapshot=Decimal(row['available']),
                actual_used_qty=Decimal(row['required']),
                unit_cost_snapshot=Decimal(row['unit_cost']),
                line_cost=Decimal(row['est_cost']),
                create_by=username,
                modified_by=username,
            )
        return order


def post_production_order(actor, order_id: int, request_key, *, consumption_overrides: dict | None = None):
    if not has_role_permissions(actor, ('manufacturing.post_production',)):
        raise PermissionDenied('You do not have permission to post production orders.')

    def _do():
        try:
            with transaction.atomic(), localcontext() as ctx:
                ctx.prec = 50
                if not get_general().enable_in_house_production:
                    raise ValidationError({'enable_in_house_production': 'In-house production is disabled.'})
                scope = lock_warehouse_scope(actor)
                if not has_role_permissions(actor, ('manufacturing.post_production',)):
                    raise PermissionDenied('You do not have permission to post production orders.')

                ProductionOrder.objects.filter(pk=order_id).update(updated_at=F('updated_at'))
                order = ProductionOrder.objects.select_for_update().select_related('bom', 'finished_product').filter(pk=order_id).first()
                if order is None:
                    raise NotFound('Production order not found.')
                check_warehouse(scope, order.material_warehouse_id)
                check_warehouse(scope, order.finished_warehouse_id)
                if order.status == ProductionOrder.Status.POSTED:
                    return IdempotentResult('production_order', order.pk, {'id': order.pk, 'status': order.status}, value=order)
                if order.status == ProductionOrder.Status.CANCELLED:
                    raise StateConflict('Cancelled production orders cannot be posted.')
                if order.bom.status != BillOfMaterials.Status.APPROVED:
                    raise ValidationError({'bom': 'BOM must be approved before posting production.'})
                if order.accepted_good_qty <= ZERO:
                    raise ValidationError({'accepted_good_qty': 'Accepted good quantity must be positive.'})

                lines = list(order.consumption_lines.select_for_update().select_related('raw_material').order_by('raw_material_id', 'pk'))
                if not lines:
                    raise ValidationError({'consumption_lines': 'Production order has no raw material lines.'})

                # Lock order: ④ raw material stock first
                rm_ids = sorted({ln.raw_material_id for ln in lines})
                RawMaterialStock.objects.filter(warehouse_id=order.material_warehouse_id, raw_material_id__in=rm_ids).update(
                    updated_at=F('updated_at'),
                )
                rm_stocks = {
                    s.raw_material_id: s
                    for s in RawMaterialStock.objects.select_for_update().filter(
                        warehouse_id=order.material_warehouse_id, raw_material_id__in=rm_ids,
                    ).order_by('raw_material_id')
                }

                username = actor.get_username()
                total_mat_cost = ZERO
                for ln in lines:
                    used = q_qty((consumption_overrides or {}).get(ln.raw_material_id, ln.actual_used_qty or ln.planned_qty))
                    if used <= ZERO:
                        raise ValidationError({'actual_used_qty': f'{ln.raw_material.code}: used quantity must be positive.'})
                    rms = rm_stocks.get(ln.raw_material_id)
                    avail = Decimal(rms.quantity) if rms else ZERO
                    if avail < used:
                        raise InsufficientStock({'raw_material': f'Shortage of raw material {ln.raw_material.code}: need {used}, available {avail}.'})
                    avg_cost = q_cost(rms.average_cost or ln.raw_material.current_unit_cost)
                    line_cost = q_total_cost(used * avg_cost)
                    rms.quantity = q_qty(avail - used)
                    rms.modified_by = username
                    rms.save(update_fields=['quantity', 'modified_by', 'updated_at'])

                    RawMaterialMovement.objects.create(
                        warehouse_id=order.material_warehouse_id,
                        raw_material_id=ln.raw_material_id,
                        kind=RawMaterialMovement.Kind.PRODUCTION_CONSUME,
                        quantity_delta=-used,
                        unit_cost=avg_cost,
                        balance_after=rms.quantity,
                        movement_date=order.production_date,
                        reference_type='production_order',
                        reference_id=order.pk,
                        posting_key=f'mfg:{order.pk}:consume:{ln.pk}',
                        create_by=username,
                        modified_by=username,
                    )
                    ln.actual_used_qty = used
                    ln.unit_cost_snapshot = avg_cost
                    ln.line_cost = line_cost
                    ln.modified_by = username
                    ln.save(update_fields=['actual_used_qty', 'unit_cost_snapshot', 'line_cost', 'modified_by', 'updated_at'])
                    total_mat_cost += line_cost

                total_cost = q_total_cost(total_mat_cost + Decimal(order.labor_cost) + Decimal(order.overhead_cost))
                unit_cost = q_cost(total_cost / Decimal(order.accepted_good_qty))

                # Then ③ finished stock output
                post_stock_movement(
                    warehouse_id=order.finished_warehouse_id,
                    product_id=order.finished_product_id,
                    kind=StockMovement.Kind.PRODUCTION_OUTPUT,
                    quantity_delta=order.accepted_good_qty,
                    unit_cost_base=unit_cost,
                    movement_date=order.production_date,
                    reference_type='production_order',
                    reference_id=order.pk,
                    posting_key=f'production_order:{order.pk}:output',
                    notes=order.notes or f'Production {order.reference_number}',
                    actor=actor,
                )

                order.material_cost_total = q_total_cost(total_mat_cost)
                order.unit_cost_calculated = unit_cost
                order.status = ProductionOrder.Status.POSTED
                order.posted_at = timezone.now()
                order.request_key = request_key
                order.modified_by = username
                order.save(update_fields=[
                    'material_cost_total', 'unit_cost_calculated', 'status',
                    'posted_at', 'request_key', 'modified_by', 'updated_at',
                ])
                log_audit_event(
                    actor, AuditLog.Action.POST, order,
                    changes={'accepted': str(order.accepted_good_qty), 'unit_cost': str(unit_cost)},
                    request_key=request_key, warehouse=order.finished_warehouse,
                )
                return IdempotentResult('production_order', order.pk, {'id': order.pk, 'unit_cost': str(unit_cost)}, value=order)
        except OperationalError as exc:
            if 'locked' in str(exc).lower() or 'deadlock' in str(exc).lower():
                raise TransferBusy() from exc
            raise

    return run_idempotent(
        request_key, f'post_production_order:{order_id}', {'order_id': order_id}, _do,
        loader=lambda r: ProductionOrder.objects.get(pk=r.result_id),
    )
