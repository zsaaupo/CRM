from collections import defaultdict
from decimal import Decimal, localcontext

from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from accounting.services import post_chalan_payment
from inventory.models import Product
from warehouse.models import WareHouse
from warehouse.services import deduct_stock, restock

from .models import Courier, Delivery, DeliveryStatus, DeliveryStatusUpdate, PackingSlip


STATUS_TRANSITIONS = {
    DeliveryStatus.ASSIGNED: (DeliveryStatus.HANDED_OVER, DeliveryStatus.CANCELLED),
    DeliveryStatus.HANDED_OVER: (DeliveryStatus.IN_TRANSIT, DeliveryStatus.DELIVERED, DeliveryStatus.FAILED, DeliveryStatus.RETURNED),
    DeliveryStatus.IN_TRANSIT: (DeliveryStatus.DELIVERED, DeliveryStatus.FAILED, DeliveryStatus.RETURNED),
    DeliveryStatus.FAILED: (DeliveryStatus.IN_TRANSIT, DeliveryStatus.DELIVERED, DeliveryStatus.RETURNED),
    DeliveryStatus.CANCELLED: (DeliveryStatus.ASSIGNED,),
}
REASSIGNABLE_STATUSES = (DeliveryStatus.ASSIGNED, DeliveryStatus.CANCELLED)


class DeliveryConflict(APIException):
    status_code = 409
    default_code = 'delivery_conflict'


def active_courier(pk):
    courier = Courier.objects.select_for_update().filter(pk=pk, is_active=True).first()
    if courier is None:
        raise ValidationError({'courier': 'Choose an active courier.'})
    return courier


def assign_delivery(chalan, data, actor):
    """Call with the Chalan locked inside a transaction."""
    existing = Delivery.objects.filter(chalan=chalan).first()
    if existing:
        if existing.courier_id == data['courier'].pk and existing.tracking_number == data['tracking_number']:
            return existing, False
        raise DeliveryConflict('This Chalan already has a delivery. Open the delivery to update it.')

    delivery = Delivery.objects.create(
        chalan=chalan, courier=active_courier(data['courier'].pk), tracking_number=data['tracking_number'],
        create_by=actor, modified_by=actor,
    )
    DeliveryStatusUpdate.objects.create(delivery=delivery, status=delivery.status, create_by=actor)
    return delivery, True


def update_delivery(delivery, data, actor):
    fields = ['modified_by', 'updated_at']
    courier = data.get('courier')
    if courier and courier.pk != delivery.courier_id:
        if delivery.status not in REASSIGNABLE_STATUSES:
            raise DeliveryConflict('The courier cannot be changed after handover.')
        delivery.courier = active_courier(courier.pk)
        fields.append('courier')
        data.setdefault('tracking_number', '')
    if 'tracking_number' in data and delivery.tracking_number != data['tracking_number']:
        delivery.tracking_number = data['tracking_number']
        fields.append('tracking_number')
    if len(fields) > 2:
        delivery.modified_by = actor
        delivery.save(update_fields=fields)
    return delivery


def adjust_delivery_stock(chalan, actor, now, *, returning=False):
    warehouse = WareHouse.objects.select_for_update().filter(pk=chalan.warehouse_id, is_active=True).first()
    if warehouse is None:
        raise ValidationError({'warehouse': 'The Chalan warehouse must be active to post stock.'})
    items = list(chalan.items.all())
    if not items:
        raise ValidationError({'items': 'Add at least one item to the Chalan before posting stock.'})
    products = {product.pk: product for product in Product.objects.select_for_update().filter(
        pk__in={item.product_id for item in items}).order_by('pk')}
    with localcontext() as context:
        context.prec = 50
        quantities = defaultdict(Decimal)
        for item in items:
            product = products[item.product_id]
            if item.unit_id != product.unit_id:
                raise ValidationError({'items': f'Line {item.line_number}: the Chalan unit must match the product unit.'})
            quantities[item.product_id] += item.quantity
        if returning:
            restock(warehouse, quantities, actor, now)
        else:
            deduct_stock(warehouse, quantities, products, actor, now)


def transition_delivery(delivery, data, actor, scope):
    """Apply the delivery status and its postings with the Chalan locked."""
    target = data['status']
    if target == delivery.status:
        return False
    if data.get('expected_status', delivery.status) != delivery.status:
        raise DeliveryConflict('The delivery status has changed. Refresh the page and try again.')
    if target not in STATUS_TRANSITIONS.get(delivery.status, ()):
        raise DeliveryConflict(f'A delivery marked {delivery.get_status_display()} cannot move to {DeliveryStatus(target).label}.')
    now = timezone.now()
    fields = ['status', 'modified_by', 'updated_at']
    if target == DeliveryStatus.HANDED_OVER:
        if not PackingSlip.objects.filter(chalan_id=delivery.chalan_id).exists():
            raise ValidationError({'status': 'Generate a packing slip from the Chalan before handing it over.'})
        active_courier(delivery.courier_id)
        adjust_delivery_stock(delivery.chalan, actor, now)
        delivery.stock_deducted_at = now
        fields.append('stock_deducted_at')
    elif target == DeliveryStatus.DELIVERED:
        post_chalan_payment(delivery.chalan, scope, actor, data.get('payment_account'), date=timezone.localdate(now))
    elif target == DeliveryStatus.RETURNED and delivery.stock_deducted_at is not None:
        adjust_delivery_stock(delivery.chalan, actor, now, returning=True)

    delivery.status = target
    delivery.modified_by = actor.get_username()
    delivery.save(update_fields=fields)
    DeliveryStatusUpdate.objects.create(delivery=delivery, status=target, note=data['note'],
                                        created_at=now, create_by=delivery.modified_by)
    return True
