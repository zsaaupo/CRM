"""Idempotent opening stock posting command (D-16)."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from common.audit import log_audit_event
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog
from common.money import q_cost, q_qty
from inventory.models import Product, UnitOfMeasure, check_units_convertible
from warehouse.models import StockMovement, WareHouse
from warehouse.scope import check_warehouse, lock_warehouse_scope
from warehouse.services import post_stock_movement
from warehouse.tracking import receive_tracking


def post_opening_stock(actor, rows: list[dict], request_key, *, request=None) -> tuple[dict, bool]:
    if not rows:
        raise ValidationError({'rows': 'Supply at least one opening stock row.'})
    if not has_role_permissions(actor, ('warehouse.view_stock',)):
        raise PermissionDenied('You do not have permission to post opening stock.')

    def _do():
        with transaction.atomic():
            scope = lock_warehouse_scope(actor)
            if not has_role_permissions(actor, ('warehouse.view_stock',)):
                raise PermissionDenied('You do not have permission to post opening stock.')

            movement_ids = []
            for idx, row in enumerate(rows, start=1):
                wh_id = row.get('warehouse') or row.get('warehouse_id')
                prod_id = row.get('product') or row.get('product_id')
                if not wh_id or not prod_id:
                    raise ValidationError({'rows': f'Row {idx}: warehouse and product are required.'})
                wh = WareHouse.objects.filter(pk=wh_id, is_active=True).first()
                if wh is None:
                    raise ValidationError({'rows': f'Row {idx}: choose an active warehouse.'})
                check_warehouse(scope, wh.pk)

                product = Product.objects.select_for_update().filter(pk=prod_id).first()
                if product is None:
                    raise ValidationError({'rows': f'Row {idx}: product not found.'})
                if product.product_type_id in ('service', 'digital', 'combo'):
                    raise ValidationError({'rows': f'Row {idx}: opening stock cannot be posted for {product.product_type_id} products.'})

                qty = q_qty(row.get('quantity') or 0)
                if qty <= 0:
                    raise ValidationError({'rows': f'Row {idx}: quantity must be positive.'})

                unit_id = row.get('unit') or row.get('unit_id') or product.unit_id
                unit = UnitOfMeasure.objects.filter(pk=unit_id).first()
                if unit is None:
                    raise ValidationError({'rows': f'Row {idx}: unit not found.'})
                factor = check_units_convertible(unit, product.unit)
                base_qty = q_qty(qty * factor)

                raw_cost = row.get('unit_cost_base')
                if raw_cost is not None and raw_cost != '':
                    unit_cost_base = q_cost(Decimal(str(raw_cost)) / factor)
                else:
                    unit_cost_base = q_cost(product.cost_price or 0)

                batch_number = (row.get('batch_number') or '').strip()
                expiry_date = row.get('expiry_date')
                serials = row.get('serials') or []
                posting_key = f'opening:{wh.pk}:{product.pk}:{batch_number or "base"}'

                if StockMovement.objects.filter(posting_key=posting_key).exists():
                    existing = StockMovement.objects.get(posting_key=posting_key)
                    movement_ids.append(existing.pk)
                    continue

                batch_obj, serial_objs = receive_tracking(
                    product,
                    wh.pk,
                    base_qty,
                    batch_number=batch_number,
                    expiry_date=expiry_date,
                    unit_cost=unit_cost_base,
                    serials=serials,
                    actor=actor,
                )
                mv = post_stock_movement(
                    warehouse_id=wh.pk,
                    product_id=product.pk,
                    kind=StockMovement.Kind.OPENING,
                    quantity_delta=base_qty,
                    unit_cost_base=unit_cost_base,
                    batch_id=batch_obj.pk if batch_obj else None,
                    serial_ids=[s.pk for s in serial_objs],
                    movement_date=row.get('movement_date') or timezone.localdate(),
                    reference_type='opening',
                    reference_id=product.pk,
                    posting_key=posting_key,
                    notes=row.get('notes', 'Opening stock'),
                    actor=actor,
                )
                movement_ids.append(mv.pk)

            log_audit_event(
                actor, AuditLog.Action.POST, module=AuditLog.Module.WAREHOUSE,
                changes={'count': len(movement_ids), 'movement_ids': movement_ids},
                request=request, request_key=request_key, object_repr=f'opening_stock:{len(movement_ids)}',
            )
            return IdempotentResult(
                result_type='opening_stock',
                result_id=movement_ids[0] if movement_ids else 0,
                result_data={'movement_ids': movement_ids, 'count': len(movement_ids)},
            )

    return run_idempotent(request_key, 'opening_stock', {'rows': rows}, _do)
