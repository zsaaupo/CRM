from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from accounting.serializers import PaymentAccountField
from common.models import SettlementMode, TradeStatus
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from inventory.models import Currency
from people.models import Party
from warehouse.scope import WarehouseScopedSerializerMixin
from .models import SalesOrder, SalesOrderItem
from .services import lock_order, require_item_permission


class SalesOrderSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default=None)
    customer = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.filter(is_customer=True), required=False, allow_null=True,
    )
    customer_name = serializers.CharField(source='customer.name', read_only=True, default=None)
    customer_code = serializers.CharField(source='customer.code', read_only=True, default=None)
    payment_account = PaymentAccountField(required=False, allow_null=True)
    payment_account_name = serializers.CharField(source='payment_account.name', read_only=True, default=None)
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all())
    status = serializers.SlugRelatedField(slug_field='code', queryset=TradeStatus.objects.all(), required=False)
    subtotal = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    grand_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            'id', 'order_number', 'customer', 'customer_name', 'customer_code', 'order_date', 'expected_delivery_date', 'currency',
            'exchange_rate', 'status', 'warehouse', 'warehouse_name', 'payment_account', 'payment_account_name',
            'settlement_mode', 'payment_status', 'recognized_total_base',
            'order_discount_type', 'order_discount_value', 'order_discount_amount',
            'order_tax_rule', 'order_tax_rate', 'order_tax_amount',
            'biller', 'biller_name_snapshot', 'attachment', 'staff_note', 'sale_note',
            'customer_reference',
            'billing_address', 'shipping_address', 'shipping_cost', 'notes', 'confirmed_at', 'fulfilled_at', 'is_completed',
            'subtotal', 'tax_total', 'grand_total', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'order_number', 'confirmed_at', 'fulfilled_at', 'is_completed',
                            'payment_status', 'recognized_total_base', 'order_discount_amount', 'order_tax_amount',
                            'biller_name_snapshot', 'subtotal', 'tax_total', 'grand_total']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        mode = attrs.get('settlement_mode', self.instance.settlement_mode if self.instance else SettlementMode.AUTO_FULL)
        account = attrs.get('payment_account', self.instance.payment_account if self.instance else None)
        if mode == SettlementMode.AUTO_FULL and account is None:
            if self.instance is None or 'payment_account' in attrs or 'settlement_mode' in attrs:
                raise serializers.ValidationError({'payment_account': ['Choose a payment account.']})
        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        current = lock_order(instance.pk, self.context['request'].user)
        self.instance = current
        return super().update(current, self.validate(validated_data))


class SalesOrderItemSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    warehouse_parent_field = 'sales_order'
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
            'quantity', 'unit_conversion_factor', 'base_quantity', 'batch', 'serial_numbers',
            'unit_price', 'discount_amount', 'tax_rate', 'tax_method',
            'allocated_order_discount', 'allocated_order_tax', 'net_amount',
            'tax_amount', 'line_total', 'product_name', 'product_sku', 'unit_name', 'unit_code', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'unit_conversion_factor', 'base_quantity',
                            'allocated_order_discount', 'allocated_order_tax',
                            'net_amount', 'tax_amount', 'line_total']

    def validate_sales_order(self, value):
        if self.instance and value.pk != self.instance.sales_order_id:
            raise serializers.ValidationError('An existing item cannot be moved to another order.')
        return value

    @transaction.atomic
    def create(self, validated_data):
        lock_order(validated_data['sales_order'].pk, self.context['request'].user)
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        lock_order(instance.sales_order_id, self.context['request'].user)
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


class SaleCompleteSerializer(serializers.Serializer):
    sale_id = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    is_completed = serializers.BooleanField(required=True)
    settlement_mode = serializers.ChoiceField(choices=SettlementMode.choices, required=False)
    tenders = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    request_key = serializers.UUIDField(required=False)
    payment_date = serializers.DateField(required=False)

    def validate_is_completed(self, value):
        if value is not True:
            raise serializers.ValidationError('This field must be true to complete the sale.')
        return value


class SalesOrderLineSerializer(serializers.ModelSerializer):
    """Validate an unsaved line before its order exists."""

    parent_field = 'sales_order'
    id = serializers.IntegerField(required=False, min_value=1)

    class Meta:
        model = SalesOrderItem
        fields = ['id', 'line_number', 'product', 'description', 'unit', 'quantity', 'unit_price',
                  'discount_amount', 'tax_rate', 'tax_method', 'batch', 'serial_numbers']

    def validate(self, attrs):
        for field in ('discount_amount', 'tax_rate'):
            attrs.setdefault(field, 0)
        attrs.setdefault('description', '')
        attrs.setdefault('serial_numbers', [])
        candidate = self.Meta.model(**{name: value for name, value in attrs.items() if name != 'id'})
        try:
            candidate.full_clean(exclude=[self.parent_field, *AUDIT_FIELDS])
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
        order = lock_order(instance.pk, self.context['request'].user)
        items = validated_data.pop('items', None)
        self.instance = order
        validated_data = self.validate(validated_data)
        if items is not None:
            request = self.context.get('request')
            actor = validated_data.get('modified_by', order.modified_by)
            replace_sales_lines(order, items, actor, lambda action: require_item_permission(request, action))
        return super().update(order, validated_data)


def replace_sales_lines(document, items, actor, require_permission):
    """Replace nested lines while retaining IDs and audit history for existing lines."""
    relation = document._meta.get_field('items')
    existing = {item.pk: item for item in document.items.all()}
    ids = [item['id'] for item in items if 'id' in item]
    if len(set(ids)) != len(ids) or any(pk not in existing for pk in ids):
        raise serializers.ValidationError({
            'items': [f'Item IDs must be unique and belong to this {document._meta.verbose_name}.'],
        })
    removed = set(existing) - set(ids)
    if removed:
        require_permission('delete')
    if any('id' not in item for item in items):
        require_permission('add')
    changed = set()
    for item in items:
        if 'id' not in item:
            continue
        original = existing[item['id']]
        if any(getattr(original, key + '_id' if key in ('product', 'unit') else key) !=
               (value.pk if key in ('product', 'unit') else value)
               for key, value in item.items() if key != 'id'):
            changed.add(item['id'])
    if changed:
        require_permission('change')
    document.items.filter(pk__in=removed).delete()
    # Free final line numbers first so swaps retain IDs and creation history.
    occupied = {existing[pk].line_number for pk in ids} | {item['line_number'] for item in items}
    temporary = 1
    for pk in sorted(changed):
        while temporary in occupied:
            temporary += 1
        document.items.filter(pk=pk).update(line_number=temporary)
        occupied.add(temporary)
    now = timezone.now()
    for item in items:
        values = {key: value for key, value in item.items() if key != 'id'}
        if 'id' not in item:
            relation.related_model.objects.create(
                **{relation.field.name: document}, create_by=actor, modified_by=actor, **values)
        elif item['id'] in changed:
            obj = existing[item['id']]
            for k, v in values.items():
                setattr(obj, k, v)
            obj.sync_base_quantity()
            document.items.filter(pk=item['id']).update(
                **values,
                unit_conversion_factor=obj.unit_conversion_factor,
                base_quantity=obj.base_quantity,
                modified_by=actor,
                updated_at=now,
            )
