from decimal import Decimal, localcontext

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied

from account.permissions import has_role_permissions
from accounting.models import base_currency
from accounting.serializers import PaymentAccountField
from common.models import TradeStatus
from audit_log.service import record_activity
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from inventory.models import Currency
from people.models import Party
from warehouse.models import WareHouse
from warehouse.scope import WarehouseScopedSerializerMixin, warehouse_scope

from .models import (Chalan, ChalanItem, Courier, Delivery, DeliveryStatus, DeliveryStatusUpdate,
                     PackingSlip, SalesOrder, SalesOrderItem)
from .permissions import CHALAN_ITEM_VIEW, PACKING_SLIP_VIEW
from .services import lock_chalan, lock_order, require_editable_chalan, require_item_permission


class SalesOrderSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default=None)
    customer = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.filter(is_customer=True), required=False, allow_null=True,
    )
    customer_name = serializers.CharField(source='customer.name', read_only=True, default=None)
    customer_code = serializers.CharField(source='customer.code', read_only=True, default=None)
    payment_account = PaymentAccountField()
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
            'customer_reference',
            'billing_address', 'shipping_address', 'shipping_cost', 'notes', 'confirmed_at', 'fulfilled_at', 'is_completed',
            'subtotal', 'tax_total', 'grand_total', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'order_number', 'confirmed_at', 'fulfilled_at', 'is_completed',
                            'subtotal', 'tax_total', 'grand_total']

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
                  'discount_amount', 'tax_rate']

    def validate(self, attrs):
        for field in ('discount_amount', 'tax_rate'):
            attrs.setdefault(field, 0)
        attrs.setdefault('description', '')
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
            document.items.filter(pk=item['id']).update(**values, modified_by=actor, updated_at=now)
            record_activity(existing[item['id']], 'updated', actor=actor)


class ChalanSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Party.objects.filter(is_customer=True))
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    customer_code = serializers.CharField(source='customer.code', read_only=True)
    warehouse = serializers.PrimaryKeyRelatedField(queryset=WareHouse.objects.all(), required=False)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all(), required=False)
    is_delivered = serializers.BooleanField(read_only=True)
    stock_in_transit = serializers.BooleanField(read_only=True)
    is_delete_locked = serializers.BooleanField(read_only=True)
    subtotal = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    grand_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    class Meta:
        model = Chalan
        fields = [
            'id', 'chalan_number', 'customer', 'customer_name', 'customer_code', 'warehouse', 'warehouse_name',
            'chalan_date', 'expected_delivery_date', 'customer_reference', 'recipient_name', 'recipient_phone',
            'shipping_address', 'notes', 'currency', 'exchange_rate', 'shipping_cost',
            'subtotal', 'tax_total', 'grand_total', 'is_delivered', 'stock_in_transit', 'is_delete_locked', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'chalan_number']

    def get_fields(self):
        fields = super().get_fields()
        if isinstance(self.instance, Chalan):
            fields['customer'].queryset = Party.all_objects.filter(
                Q(is_customer=True, is_deleted=False) | Q(pk=self.instance.customer_id),
            )
        return fields

    def validate(self, attrs):
        currency = attrs.get('currency')
        if currency and (self.instance is None or currency.code != self.instance.currency_id):
            if 'exchange_rate' not in attrs:
                if currency.code != base_currency():
                    raise serializers.ValidationError({'exchange_rate': 'Enter an exchange rate for this currency.'})
                attrs['exchange_rate'] = Decimal('1')
        return super().validate(attrs)


CHALAN_PRICE_FIELDS = ('unit_price', 'discount_amount', 'tax_rate')


class ChalanItemSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False, min_value=1, max_value=2**63 - 1)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)
    net_amount = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    line_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    class Meta:
        model = ChalanItem
        fields = ['id', 'line_number', 'product', 'unit', 'quantity', 'description', *CHALAN_PRICE_FIELDS,
                  'net_amount', 'tax_amount', 'line_total',
                  'product_name', 'product_sku', 'unit_code', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS[1:]

    def validate(self, attrs):
        attrs.setdefault('description', '')
        candidate = ChalanItem(**{name: value for name, value in attrs.items() if name != 'id'})
        excluded = ['chalan', *AUDIT_FIELDS]
        if 'id' in attrs:
            excluded.extend(field for field in CHALAN_PRICE_FIELDS if field not in attrs)
        try:
            candidate.full_clean(exclude=excluded)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs


class ChalanDetailSerializer(ChalanSerializer):
    items = ChalanItemSerializer(many=True, read_only=True)

    class Meta(ChalanSerializer.Meta):
        fields = [*ChalanSerializer.Meta.fields, 'items']

    def get_fields(self):
        fields = super().get_fields()
        if not has_role_permissions(self.context['request'].user, CHALAN_ITEM_VIEW):
            fields.pop('items')
        return fields


class ChalanWriteSerializer(ChalanSerializer):
    items = ChalanItemSerializer(many=True, allow_empty=False, write_only=True)

    class Meta(ChalanSerializer.Meta):
        fields = [*ChalanSerializer.Meta.fields, 'items']

    def validate_items(self, items):
        if self.instance is None and any('id' in item for item in items):
            raise serializers.ValidationError('New Chalan items must not include existing IDs.')
        numbers = {item['line_number'] for item in items}
        if len(numbers) != len(items):
            raise serializers.ValidationError('Line numbers must be unique within this Chalan.')
        return items

    def validate(self, attrs):
        if self.instance is not None:
            require_editable_chalan(self.instance)
        items = attrs.pop('items', None)
        attrs = super().validate(attrs)
        if items is not None:
            attrs['items'] = items
        return attrs

    def save_items(self, chalan, items, actor):
        user = self.context['request'].user

        def require_permission(action):
            if not has_role_permissions(user, (f'sale.{action}_chalanitem',)):
                raise PermissionDenied(f'You do not have permission to {action} Chalan items.')

        previous = {
            pk: amounts for pk, *amounts in chalan.items.values_list('pk', *CHALAN_PRICE_FIELDS)
        }
        expected = {}
        errors = []
        for item in items:
            stored = previous.get(item.get('id'), (Decimal('0'),) * len(CHALAN_PRICE_FIELDS))
            price, discount, rate = (item.get(field, value) for field, value in zip(CHALAN_PRICE_FIELDS, stored))
            quantity = item['quantity']
            expected[item['line_number']] = (quantity, price, discount, rate)
            with localcontext() as context:
                context.prec = 50
                errors.append({'discount_amount': ['Discount cannot exceed quantity times unit price.']}
                              if discount > quantity * price else {})
        if any(errors):
            raise serializers.ValidationError({'items': errors})

        replace_sales_lines(chalan, items, actor, require_permission)
        actual = {
            number: (quantity, price, discount, rate)
            for number, quantity, price, discount, rate in chalan.items.values_list(
                'line_number', 'quantity', *CHALAN_PRICE_FIELDS,
            )
        }
        if actual != expected:
            raise serializers.ValidationError({'items': 'The database could not store the quantities or amounts exactly.'})

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop('items')
        chalan = super().create(validated_data)
        self.save_items(chalan, items, chalan.create_by)
        return chalan

    @transaction.atomic
    def update(self, instance, validated_data):
        self.instance = current = lock_chalan(warehouse_scope(self.context['request'].user), pk=instance.pk)
        values = self.validate(validated_data)
        items = values.pop('items', None)
        if current.stock_in_transit:
            self.validate_dispatched_stock(current, values, items)
        chalan = super().update(current, values)
        if items is not None:
            self.save_items(chalan, items, chalan.modified_by)
        return chalan

    @staticmethod
    def validate_dispatched_stock(chalan, values, items):
        if 'warehouse' in values and values['warehouse'].pk != chalan.warehouse_id:
            raise serializers.ValidationError({'warehouse': 'The warehouse is locked while stock is with the courier.'})
        if items is not None:
            dispatched = {pk: (product, unit, quantity) for pk, product, unit, quantity in chalan.items.values_list(
                'pk', 'product_id', 'unit_id', 'quantity',
            )}
            submitted = {item.get('id'): (item['product'].pk, item['unit'].pk, item['quantity']) for item in items}
            if dispatched != submitted:
                raise serializers.ValidationError({'items': 'Products and quantities are locked while stock is with the courier.'})


class PackingSlipSerializer(serializers.ModelSerializer):
    chalan_number = serializers.CharField(source='chalan.chalan_number', read_only=True)
    customer_name = serializers.CharField(source='chalan.customer.name', read_only=True)
    warehouse_name = serializers.CharField(source='chalan.warehouse.name', read_only=True)

    class Meta:
        model = PackingSlip
        fields = ('id', 'slip_number', 'chalan', 'chalan_number', 'customer_name', 'warehouse_name', 'created_at')
        read_only_fields = fields


class CourierSerializer(CatalogSerializer):
    class Meta:
        model = Courier
        fields = ['id', 'name', 'contact_person', 'phone', 'email', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class DeliverySerializer(serializers.ModelSerializer):
    chalan_number = serializers.CharField(source='chalan.chalan_number', read_only=True)
    customer_name = serializers.CharField(source='chalan.customer.name', read_only=True)
    warehouse_name = serializers.CharField(source='chalan.warehouse.name', read_only=True)
    courier_name = serializers.CharField(source='courier.name', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Delivery
        fields = ['id', 'chalan', 'chalan_number', 'customer_name', 'warehouse_name', 'courier', 'courier_name',
                  'tracking_number', 'status', 'status_label', 'stock_deducted_at', *AUDIT_FIELDS[1:]]
        read_only_fields = fields


class DeliveryStatusUpdateSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = DeliveryStatusUpdate
        fields = ('id', 'status', 'status_label', 'note', 'created_at', 'create_by')
        read_only_fields = fields


class DeliveryDetailSerializer(DeliverySerializer):
    status_updates = DeliveryStatusUpdateSerializer(many=True, read_only=True)
    packing_slip = serializers.IntegerField(source='chalan.packing_slip.pk', read_only=True, default=None)
    packing_slip_number = serializers.CharField(source='chalan.packing_slip.slip_number', read_only=True, default=None)
    payment_account_name = serializers.CharField(source='chalan.delivery_payment.account.name', read_only=True, default=None)
    payment_amount = serializers.DecimalField(source='chalan.delivery_payment.amount', max_digits=15, decimal_places=4,
                                              read_only=True, default=None)
    payment_currency = serializers.CharField(source='chalan.delivery_payment.account.currency', read_only=True, default=None)

    class Meta(DeliverySerializer.Meta):
        fields = [*DeliverySerializer.Meta.fields, 'status_updates', 'packing_slip', 'packing_slip_number',
                  'payment_account_name', 'payment_amount', 'payment_currency']
        read_only_fields = fields

    def get_fields(self):
        fields = super().get_fields()
        if not has_role_permissions(self.context['request'].user, PACKING_SLIP_VIEW):
            fields.pop('packing_slip')
            fields.pop('packing_slip_number')
        return fields


class DeliveryAssignmentSerializer(serializers.Serializer):
    courier = serializers.PrimaryKeyRelatedField(queryset=Courier.objects.all())
    tracking_number = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')


class DeliveryTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=DeliveryStatus.choices)
    expected_status = serializers.ChoiceField(choices=DeliveryStatus.choices, required=False)
    payment_account = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
