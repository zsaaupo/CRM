"""Batch/lot and IMEI/serial allocation and lifecycle tracking."""

from decimal import Decimal

from django.db import IntegrityError, connection
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from common.settings_store import get_general
from .models import StockBatch, StockSerial


def allocate_tracking(
    product,
    warehouse_id: int,
    base_qty: Decimal,
    batch_id: int | None = None,
    serials: list[str] | None = None,
    *,
    doc_date=None,
):
    """Validate and lock outbound batch and/or serial tracking before posting stock."""
    if not connection.in_atomic_block:
        raise RuntimeError('allocate_tracking must run inside transaction.atomic().')

    qty = Decimal(base_qty)
    check_date = doc_date or timezone.localdate()
    serial_list = [str(s).strip() for s in (serials or []) if str(s).strip()]

    batch_obj = None
    if product.has_batch_expiry:
        if not batch_id:
            raise ValidationError({'batch': f'Choose a batch for batch-tracked product {product.sku}.'})
        StockBatch.objects.filter(pk=batch_id, warehouse_id=warehouse_id, product_id=product.pk).update(updated_at=F('updated_at'))
        batch_obj = StockBatch.objects.select_for_update().filter(
            pk=batch_id, warehouse_id=warehouse_id, product_id=product.pk,
        ).first()
        if batch_obj is None:
            raise ValidationError({'batch': f'Batch #{batch_id} was not found for {product.sku} in this warehouse.'})
        if batch_obj.quantity < qty:
            raise ValidationError({'batch': f'Batch {batch_obj.batch_number} only has {batch_obj.quantity} available.'})
        if get_general().block_expired_batch_sale and batch_obj.expiry_date and batch_obj.expiry_date < check_date:
            raise ValidationError({'batch': f'Batch {batch_obj.batch_number} expired on {batch_obj.expiry_date}.'})
    elif batch_id:
        StockBatch.objects.filter(pk=batch_id, warehouse_id=warehouse_id, product_id=product.pk).update(updated_at=F('updated_at'))
        batch_obj = StockBatch.objects.select_for_update().filter(
            pk=batch_id, warehouse_id=warehouse_id, product_id=product.pk,
        ).first()
        if batch_obj is None:
            raise ValidationError({'batch': f'Batch #{batch_id} does not belong to {product.sku} in this warehouse.'})

    serial_objs = []
    if product.has_imei_serial:
        if qty != qty.to_integral_value() or qty <= 0:
            raise ValidationError({'serials': f'Serial-tracked product {product.sku} requires a positive integer quantity.'})
        expected_count = int(qty)
        if len(serial_list) != expected_count:
            raise ValidationError({'serials': f'{product.sku} requires exactly {expected_count} serial number(s); received {len(serial_list)}.'})
        if len(set(serial_list)) != len(serial_list):
            raise ValidationError({'serials': f'Duplicate serial numbers supplied for {product.sku}.'})
        StockSerial.objects.filter(product_id=product.pk, serial_number__in=serial_list).update(updated_at=F('updated_at'))
        found = {
            s.serial_number: s
            for s in StockSerial.objects.select_for_update().filter(
                product_id=product.pk, serial_number__in=serial_list,
            )
        }
        missing = [s for s in serial_list if s not in found]
        if missing:
            raise ValidationError({'serials': f'Unknown serial number(s) for {product.sku}: {", ".join(missing)}.'})
        for sn in serial_list:
            obj = found[sn]
            if obj.warehouse_id != warehouse_id or obj.status != StockSerial.Status.IN_STOCK:
                raise ValidationError({'serials': f'Serial {sn} is not in stock at this warehouse (status: {obj.status}).'})
            if batch_obj is not None and obj.batch_id != batch_obj.pk:
                raise ValidationError({'serials': f'Serial {sn} does not belong to batch {batch_obj.batch_number}.'})
            serial_objs.append(obj)

    return batch_obj, serial_objs


def receive_tracking(
    product,
    warehouse_id: int,
    base_qty: Decimal,
    *,
    batch_number: str = '',
    expiry_date=None,
    unit_cost: Decimal = Decimal('0'),
    serials: list[str] | None = None,
    actor=None,
):
    """Create or increment batch and/or create incoming serials on receipt or opening."""
    if not connection.in_atomic_block:
        raise RuntimeError('receive_tracking must run inside transaction.atomic().')

    qty = Decimal(base_qty)
    username = actor.get_username() if actor is not None else 'system'
    batch_obj = None
    clean_batch = (batch_number or '').strip()

    if product.has_batch_expiry and not clean_batch:
        raise ValidationError({'batch_number': f'Batch number is required for batch-tracked product {product.sku}.'})

    if clean_batch:
        StockBatch.objects.filter(
            warehouse_id=warehouse_id, product_id=product.pk, batch_number=clean_batch,
        ).update(updated_at=F('updated_at'))
        batch_obj = StockBatch.objects.select_for_update().filter(
            warehouse_id=warehouse_id, product_id=product.pk, batch_number=clean_batch,
        ).first()
        if batch_obj is None:
            batch_obj = StockBatch.objects.create(
                warehouse_id=warehouse_id,
                product_id=product.pk,
                batch_number=clean_batch,
                expiry_date=expiry_date,
                quantity=Decimal('0'),
                unit_cost=unit_cost,
                create_by=username,
                modified_by=username,
            )
        elif expiry_date and not batch_obj.expiry_date:
            batch_obj.expiry_date = expiry_date
            batch_obj.modified_by = username
            batch_obj.save(update_fields=['expiry_date', 'modified_by', 'updated_at'])

    serial_list = [str(s).strip() for s in (serials or []) if str(s).strip()]
    serial_objs = []
    if product.has_imei_serial:
        if qty != qty.to_integral_value() or qty <= 0:
            raise ValidationError({'serials': f'Serial-tracked product {product.sku} requires an integer base quantity.'})
        if len(serial_list) != int(qty):
            raise ValidationError({'serials': f'{product.sku} requires exactly {int(qty)} serial number(s).'})
        if len(set(serial_list)) != len(serial_list):
            raise ValidationError({'serials': f'Duplicate serial number supplied for {product.sku}.'})
        for sn in serial_list:
            try:
                obj = StockSerial.objects.create(
                    warehouse_id=warehouse_id,
                    product_id=product.pk,
                    batch=batch_obj,
                    serial_number=sn,
                    status=StockSerial.Status.IN_STOCK,
                    create_by=username,
                    modified_by=username,
                )
            except IntegrityError as exc:
                raise ValidationError({'serials': f'Serial {sn} already exists for {product.sku}.'}) from exc
            serial_objs.append(obj)

    return batch_obj, serial_objs
