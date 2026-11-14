from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from common.models import Status
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from inventory.models import Currency
from people.models import Party
from .models import PurchaseOrder, PurchaseOrderItem


class PurchaseOrderSerializer(CatalogSerializer):
    supplier = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.filter(is_supplier=True), required=False, allow_null=True,
    )
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, default=None)
    supplier_code = serializers.CharField(source='supplier.code', read_only=True, default=None)
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all())
    status = serializers.SlugRelatedField(slug_field='code', queryset=Status.objects.all(), required=False)
    subtotal = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    grand_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'order_number', 'supplier', 'supplier_name', 'supplier_code', 'order_date', 'expected_date', 'currency',
            'exchange_rate', 'status', 'supplier_reference', 'shipping_cost', 'notes',
            'subtotal', 'tax_total', 'grand_total', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'order_number', 'subtotal', 'tax_total', 'grand_total']


class PurchaseOrderItemSerializer(CatalogSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)
    net_amount = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    line_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

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


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    """Validate an unsaved line before its order exists."""

    class Meta:
        model = PurchaseOrderItem
        fields = ['line_number', 'product', 'description', 'unit', 'quantity', 'unit_price',
                  'discount_amount', 'tax_rate']

    def validate(self, attrs):
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
