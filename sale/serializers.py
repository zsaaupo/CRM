from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from inventory.models import Currency
from people.models import Party
from .models import SalesOrder, SalesOrderItem
from .services import lock_draft_order, require_item_permission


class SalesOrderSerializer(CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default=None)
    customer = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.filter(is_customer=True), required=False, allow_null=True,
    )
    customer_name = serializers.CharField(source='customer.name', read_only=True, default=None)
    customer_code = serializers.CharField(source='customer.code', read_only=True, default=None)
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all())
    status = serializers.SlugRelatedField(slug_field='code', read_only=True)
    subtotal = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    grand_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            'id', 'order_number', 'customer', 'customer_name', 'customer_code', 'order_date', 'expected_delivery_date', 'currency',
            'exchange_rate', 'status', 'warehouse', 'warehouse_name', 'customer_reference',
            'billing_address', 'shipping_address', 'shipping_cost', 'notes', 'confirmed_at', 'fulfilled_at',
            'subtotal', 'tax_total', 'grand_total', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'order_number', 'status', 'confirmed_at', 'fulfilled_at',
                            'subtotal', 'tax_total', 'grand_total']

    @transaction.atomic
    def update(self, instance, validated_data):
        current = lock_draft_order(instance.pk)
        self.instance = current
        return super().update(current, self.validate(validated_data))


class SalesOrderItemSerializer(CatalogSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)
    net_amount = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    line_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    class Meta:
        model = SalesOrderItem
        fields = [
            'id', 'sales_order', 'line_number', 'product', 'description', 'unit',
            'quantity', 'unit_price', 'discount_amount', 'tax_rate', 'net_amount',
            'tax_amount', 'line_total', 'product_name', 'product_sku', 'unit_name', 'unit_code', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'net_amount', 'tax_amount', 'line_total']

    def validate_sales_order(self, value):
        if self.instance and value.pk != self.instance.sales_order_id:
            raise serializers.ValidationError('An existing item cannot be moved to another order.')
        return value

    @transaction.atomic
    def create(self, validated_data):
        lock_draft_order(validated_data['sales_order'].pk)
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        lock_draft_order(instance.sales_order_id)
        try:
            current = SalesOrderItem.objects.get(pk=instance.pk)
        except SalesOrderItem.DoesNotExist as exc:
            raise NotFound('Sales order item not found.') from exc
        self.instance = current
        return super().update(current, self.validate(validated_data))


class SalesOrderDetailSerializer(SalesOrderSerializer):
    currency_name = serializers.CharField(source='currency.name', read_only=True)
    status_name = serializers.CharField(source='status.name', read_only=True)

    class Meta(SalesOrderSerializer.Meta):
        fields = [*SalesOrderSerializer.Meta.fields, 'currency_name', 'status_name']


class SalesOrderItemFilterSerializer(serializers.Serializer):
    sales_order = serializers.IntegerField(min_value=1, required=False)


class SalesOrderLineSerializer(serializers.ModelSerializer):
    """Validate an unsaved line before its order exists."""

    id = serializers.IntegerField(required=False, min_value=1)

    class Meta:
        model = SalesOrderItem
        fields = ['id', 'line_number', 'product', 'description', 'unit', 'quantity', 'unit_price',
                  'discount_amount', 'tax_rate']

    def validate(self, attrs):
        for field in ('discount_amount', 'tax_rate'):
            attrs.setdefault(field, 0)
        attrs.setdefault('description', '')
        candidate = SalesOrderItem(**attrs)
        try:
            candidate.full_clean(exclude=['sales_order', *AUDIT_FIELDS])
        except DjangoValidationError as exc:
            errors = exc.message_dict if hasattr(exc, 'message_dict') else {'__all__': exc.messages}
            raise serializers.ValidationError({
                'non_field_errors' if name == '__all__' else name: messages
                for name, messages in errors.items()
            }) from exc
        return attrs


class SalesOrderCreateSerializer(SalesOrderSerializer):
    items = SalesOrderLineSerializer(many=True, required=False, allow_empty=False, write_only=True)

    class Meta(SalesOrderSerializer.Meta):
        fields = [*SalesOrderSerializer.Meta.fields, 'items']

    def validate_items(self, items):
        seen = set()
        errors = []
        for item in items:
            if self.instance is None and 'id' in item:
                raise serializers.ValidationError('New order items must not include existing IDs.')
            line_number = item['line_number']
            errors.append({'line_number': ['Line numbers must be unique within this sale.']}
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
        if items:
            require_item_permission(self.context.get('request'), 'add')
        order = super().create(validated_data)
        for item in items:
            SalesOrderItem.objects.create(
                sales_order=order, create_by=order.create_by, modified_by=order.modified_by, **item,
            )
        return order

    @transaction.atomic
    def update(self, instance, validated_data):
        order = lock_draft_order(instance.pk)
        items = validated_data.pop('items', None)
        self.instance = order
        if items is not None:
            existing = {item.pk: item for item in order.items.all()}
            ids = [item['id'] for item in items if 'id' in item]
            if len(set(ids)) != len(ids) or any(pk not in existing for pk in ids):
                raise serializers.ValidationError({'items': ['Item IDs must be unique and belong to this sales order.']})
            request = self.context.get('request')
            removed = set(existing) - set(ids)
            if removed:
                require_item_permission(request, 'delete')
            if any('id' not in item for item in items):
                require_item_permission(request, 'add')
            changed = []
            for item in items:
                if 'id' not in item:
                    continue
                original = existing[item['id']]
                if any(getattr(original, key + '_id' if key in ('product', 'unit') else key) !=
                       (value.pk if key in ('product', 'unit') else value)
                       for key, value in item.items() if key != 'id'):
                    changed.append(item['id'])
            if changed:
                require_item_permission(request, 'change')
            order.items.filter(pk__in=removed).delete()
            # Free final line numbers first so swaps/renumbering retain IDs and creation history.
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
                    SalesOrderItem.objects.create(sales_order=order, create_by=actor, modified_by=actor, **values)
        return super().update(order, validated_data)
