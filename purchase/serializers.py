from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied

from common.models import TradeStatus
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from inventory.models import Currency
from people.models import Party
from .models import PurchaseOrder, PurchaseOrderItem
from .services import PurchaseBusy, lock_editable_purchase


class PurchaseOrderSerializer(CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default=None)
    supplier = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.filter(is_supplier=True), required=False, allow_null=True,
    )
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, default=None)
    supplier_code = serializers.CharField(source='supplier.code', read_only=True, default=None)
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all())
    status = serializers.SlugRelatedField(slug_field='code', queryset=TradeStatus.objects.all(), required=False)
    subtotal = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    grand_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = lock_editable_purchase(instance.pk)
        self.instance = instance
        return super().update(instance, self.validate(validated_data))

    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'order_number', 'supplier', 'supplier_name', 'supplier_code', 'order_date', 'expected_date', 'currency',
            'exchange_rate', 'status', 'warehouse', 'warehouse_name', 'is_received', 'supplier_reference', 'shipping_cost', 'notes',
            'subtotal', 'tax_total', 'grand_total', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'order_number', 'is_received', 'subtotal', 'tax_total', 'grand_total']


class PurchaseOrderItemSerializer(CatalogSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)
    net_amount = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    line_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    @transaction.atomic
    def create(self, validated_data):
        lock_editable_purchase(validated_data['purchase_order'].pk)
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        destination = validated_data.get('purchase_order')
        for pk in sorted({instance.purchase_order_id, destination.pk if destination else instance.purchase_order_id}):
            lock_editable_purchase(pk)
        current = PurchaseOrderItem.objects.filter(pk=instance.pk).first()
        if current is None:
            raise NotFound('Purchase order item not found.')
        if current.purchase_order_id != instance.purchase_order_id:
            raise PurchaseBusy()
        self.instance = current
        return super().update(current, self.validate(validated_data))

    class Meta:
        model = PurchaseOrderItem
        fields = [
            'id', 'purchase_order', 'line_number', 'product', 'description', 'unit',
            'quantity', 'unit_price', 'discount_amount', 'tax_rate', 'net_amount',
            'tax_amount', 'line_total', 'product_name', 'product_sku', 'unit_name', 'unit_code', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'net_amount', 'tax_amount', 'line_total']


class PurchaseOrderDetailSerializer(PurchaseOrderSerializer):
    currency_name = serializers.CharField(source='currency.name', read_only=True)
    status_name = serializers.CharField(source='status.name', read_only=True)

    class Meta(PurchaseOrderSerializer.Meta):
        fields = [*PurchaseOrderSerializer.Meta.fields, 'currency_name', 'status_name']


class PurchaseOrderItemFilterSerializer(serializers.Serializer):
    purchase_order = serializers.IntegerField(min_value=1, required=False)


class PurchaseReceiveSerializer(serializers.Serializer):
    purchase_id = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    is_received = serializers.BooleanField()

    def validate_is_received(self, value):
        if value is not True:
            raise serializers.ValidationError('Set is_received to true to receive the purchase.')
        return value


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    """Validate an unsaved line before its order exists."""
    id = serializers.IntegerField(required=False, min_value=1)

    class Meta:
        model = PurchaseOrderItem
        fields = ['id', 'line_number', 'product', 'description', 'unit', 'quantity', 'unit_price',
                  'discount_amount', 'tax_rate']

    def validate(self, attrs):
        attrs.setdefault('description', '')
        attrs.setdefault('discount_amount', Decimal('0'))
        attrs.setdefault('tax_rate', Decimal('0'))
        candidate = PurchaseOrderItem(**attrs)
        try:
            candidate.full_clean(exclude=['purchase_order', *AUDIT_FIELDS])
        except DjangoValidationError as exc:
            errors = exc.message_dict if hasattr(exc, 'message_dict') else {'__all__': exc.messages}
            raise serializers.ValidationError({
                'non_field_errors' if name == '__all__' else name: messages
                for name, messages in errors.items()
            }) from exc
        return attrs


class PurchaseOrderCreateSerializer(PurchaseOrderSerializer):
    items = PurchaseOrderLineSerializer(many=True, required=False, allow_empty=False, write_only=True)

    class Meta(PurchaseOrderSerializer.Meta):
        fields = [*PurchaseOrderSerializer.Meta.fields, 'items']

    def validate_items(self, items):
        seen = set()
        errors = []
        for item in items:
            if self.instance is None and 'id' in item:
                raise serializers.ValidationError('New purchase items must not include existing IDs.')
            line_number = item['line_number']
            errors.append({'line_number': ['Line numbers must be unique within this purchase.']}
                          if line_number in seen else {})
            seen.add(line_number)
        if any(errors):
            raise serializers.ValidationError(errors)
        return items

    def validate(self, attrs):
        # Items are a separate model, not a writable reverse relation on the header.
        items = attrs.pop('items', None)
        attrs = super().validate(attrs)
        if items is not None:
            attrs['items'] = items
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop('items', [])
        order = super().create(validated_data)
        for item in items:
            PurchaseOrderItem.objects.create(
                purchase_order=order, create_by=order.create_by, modified_by=order.modified_by, **item,
            )
        return order

    @transaction.atomic
    def update(self, instance, validated_data):
        order = lock_editable_purchase(instance.pk)
        items = validated_data.pop('items', None)
        self.instance = order
        if items is not None:
            existing = {item.pk: item for item in order.items.all()}
            ids = [item['id'] for item in items if 'id' in item]
            if len(set(ids)) != len(ids) or any(pk not in existing for pk in ids):
                raise serializers.ValidationError({'items': ['Item IDs must be unique and belong to this purchase.']})
            request = self.context.get('request')
            removed = set(existing) - set(ids)
            changed = [item['id'] for item in items if 'id' in item and any(
                getattr(existing[item['id']], key + '_id' if key in ('product', 'unit') else key) !=
                (value.pk if key in ('product', 'unit') else value)
                for key, value in item.items() if key != 'id')]
            for action, needed in [('delete', bool(removed)), ('add', any('id' not in item for item in items)),
                                   ('change', bool(changed))]:
                if needed and request is not None and not request.user.has_perm(f'purchase.{action}_purchaseorderitem'):
                    raise PermissionDenied(f'You do not have permission to {action} purchase order items.')
            order.items.filter(pk__in=removed).delete()
            # Free final line numbers before swaps, preserving IDs and creation audits.
            occupied = {existing[pk].line_number for pk in ids} | {item['line_number'] for item in items}
            temporary = 1
            for pk in changed:
                while temporary in occupied:
                    temporary += 1
                order.items.filter(pk=pk).update(line_number=temporary)
                occupied.add(temporary)
            actor = validated_data.get('modified_by', order.modified_by)
            for item in items:
                values = {key: value for key, value in item.items() if key != 'id'}
                if 'id' in item:
                    if item['id'] in changed:
                        order.items.filter(pk=item['id']).update(**values, modified_by=actor, updated_at=timezone.now())
                else:
                    PurchaseOrderItem.objects.create(purchase_order=order, create_by=actor, modified_by=actor, **values)
        return super().update(order, validated_data)
