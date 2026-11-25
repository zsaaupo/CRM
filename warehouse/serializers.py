from audit_log.service import record_activity
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import serializers

from .access import check_assignment_scope, lock_assignment_write
from .models import Stock, RoleWarehouseAccess, WareHouse, WarehouseTransfer, WarehouseTransferItem
from .scope import WarehouseScopedSerializerMixin, lock_warehouse_scope, warehouse_scope
from .services import check_transfer_warehouses, lock_transfer, require_transfer_permission, TransferCompleted


class WareHouseSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    """Apply shared model validation and read-only audit fields."""
    warehouse_is_record = True

    class Meta:
        model = WareHouse
        fields = ['id', 'name', 'address', 'phone', 'email', 'code', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class StockReadSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    unit_name = serializers.CharField(source='product.unit.name', read_only=True)
    unit_code = serializers.CharField(source='product.unit.code', read_only=True)

    class Meta:
        model = Stock
        fields = ['id', 'warehouse', 'warehouse_name', 'warehouse_code', 'product', 'product_name',
                  'product_sku', 'quantity', 'unit_name', 'unit_code', *AUDIT_FIELDS[1:]]
        read_only_fields = fields


class StockFilterSerializer(serializers.Serializer):
    warehouse = serializers.IntegerField(min_value=1, max_value=9223372036854775807, required=False)
    product = serializers.IntegerField(min_value=1, max_value=9223372036854775807, required=False)


class WarehouseTransferLineSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False, min_value=1)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)

    class Meta:
        model = WarehouseTransferItem
        fields = ['id', 'line_number', 'product', 'product_name', 'product_sku', 'unit', 'unit_name', 'unit_code', 'quantity']

    def validate(self, attrs):
        candidate = WarehouseTransferItem(**attrs)
        try:
            candidate.full_clean(exclude=['transfer', *AUDIT_FIELDS])
        except DjangoValidationError as exc:
            errors = exc.message_dict if hasattr(exc, 'message_dict') else {'__all__': exc.messages}
            raise serializers.ValidationError({'non_field_errors' if key == '__all__' else key: messages
                                               for key, messages in errors.items()}) from exc
        return attrs


class WarehouseTransferSerializer(CatalogSerializer):
    source_warehouse_name = serializers.CharField(source='source_warehouse.name', read_only=True)
    destination_warehouse_name = serializers.CharField(source='destination_warehouse.name', read_only=True)
    items = WarehouseTransferLineSerializer(many=True, required=False, allow_empty=False)
    item_count = serializers.SerializerMethodField()
    product_summary = serializers.SerializerMethodField()

    class Meta:
        model = WarehouseTransfer
        fields = ['id', 'transfer_number', 'source_warehouse', 'source_warehouse_name',
                  'destination_warehouse', 'destination_warehouse_name', 'transfer_date', 'notes',
                  'is_completed', 'item_count', 'product_summary', 'items', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'transfer_number', 'is_completed']

    def get_item_count(self, record):
        return len(record.items.all())

    def get_product_summary(self, record):
        items = list(record.items.all())
        summary = ', '.join(f'{item.product.sku} — {item.product.name}' for item in items[:3])
        return f'{summary} (+{len(items) - 3} more)' if len(items) > 3 else summary

    def validate_items(self, items):
        numbers = [item['line_number'] for item in items]
        if len(set(numbers)) != len(numbers):
            raise serializers.ValidationError('Line numbers must be unique within the transfer.')
        ids = [item['id'] for item in items if 'id' in item]
        if self.instance is None and ids:
            raise serializers.ValidationError('New transfer items must not include existing IDs.')
        if len(set(ids)) != len(ids):
            raise serializers.ValidationError('Item IDs must be unique within the transfer.')
        return items

    def validate_header(self, attrs):
        if self.instance and self.instance.is_completed:
            raise TransferCompleted()
        scope = warehouse_scope(self.context['request'].user)
        warehouses = {}
        for name in ['source_warehouse', 'destination_warehouse']:
            warehouse = attrs.get(name, getattr(self.instance, name, None))
            warehouses[name] = warehouse
            if warehouse is not None and not warehouse.is_active and (
                self.instance is None or warehouse.pk != getattr(self.instance, f'{name}_id')
            ):
                raise serializers.ValidationError({name: 'Choose an active warehouse.'})
        check_transfer_warehouses(scope,
            warehouses['source_warehouse'].pk if warehouses['source_warehouse'] else None,
            warehouses['destination_warehouse'].pk if warehouses['destination_warehouse'] else None)
        return super().validate(attrs)

    def validate(self, attrs):
        for name in ['product', 'quantity', 'unit']:
            if name in self.initial_data:
                raise serializers.ValidationError({name: 'Supply product, unit and quantity inside items.'})
        items = attrs.pop('items', None)
        if self.instance is None and items is None:
            raise serializers.ValidationError({'items': 'Add at least one product.'})
        attrs = self.validate_header(attrs)
        if items is not None:
            attrs['items'] = items
        return attrs

    def loaded_record(self, record):
        return WarehouseTransfer.objects.select_related('source_warehouse', 'destination_warehouse').prefetch_related(
            Prefetch('items', queryset=WarehouseTransferItem.objects.select_related('product', 'unit'))).get(pk=record.pk)

    @transaction.atomic
    def create(self, validated_data):
        actor = self.context['request'].user
        lock_warehouse_scope(actor)
        require_transfer_permission(actor, 'add')
        items = validated_data.pop('items')
        self.validate_items(items)
        record = super().create(self.validate_header(validated_data))
        for item in items:
            WarehouseTransferItem.objects.create(transfer=record, create_by=record.create_by,
                modified_by=record.modified_by, **item)
        return self.loaded_record(record)

    @transaction.atomic
    def update(self, instance, validated_data):
        self.instance = lock_transfer(instance.pk, self.context['request'].user)
        items = validated_data.pop('items', None)
        validated_data = self.validate_header(validated_data)
        if items is not None:
            self.validate_items(items)
            existing = {item.pk: item for item in self.instance.items.all()}
            ids = [item['id'] for item in items if 'id' in item]
            if any(pk not in existing for pk in ids):
                raise serializers.ValidationError({'items': 'Every item ID must belong to this transfer.'})
            self.instance.items.filter(pk__in=set(existing) - set(ids)).delete()
            changed = {item['id'] for item in items if 'id' in item and any(
                getattr(existing[item['id']], key + '_id' if key in ('product', 'unit') else key)
                != (value.pk if key in ('product', 'unit') else value)
                for key, value in item.items() if key != 'id')}
            # Temporary line numbers allow swaps while preserving item IDs and creation audit.
            occupied = {existing[pk].line_number for pk in ids} | {item['line_number'] for item in items}
            temporary = 1
            for pk in sorted(changed):
                while temporary in occupied:
                    temporary += 1
                self.instance.items.filter(pk=pk).update(line_number=temporary)
                occupied.add(temporary)
            actor = self.context['request'].user.get_username()
            for item in items:
                values = {key: value for key, value in item.items() if key != 'id'}
                if 'id' not in item:
                    WarehouseTransferItem.objects.create(transfer=self.instance, create_by=actor, modified_by=actor, **values)
                elif item['id'] in changed:
                    self.instance.items.filter(pk=item['id']).update(**values, modified_by=actor, updated_at=timezone.now())
                    record_activity(existing[item['id']], 'updated', actor=actor)
        record = super().update(self.instance, validated_data)
        return self.loaded_record(record)


class TransferCompleteSerializer(serializers.Serializer):
    transfer_id = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    is_completed = serializers.BooleanField()

    def validate_is_completed(self, value):
        if not value:
            raise serializers.ValidationError('Set is_completed to true to complete the transfer.')
        return value


class RoleWarehouseAccessSerializer(CatalogSerializer):
    role_name = serializers.CharField(source='role.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default=None)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True, default=None)

    class Meta:
        model = RoleWarehouseAccess
        fields = ['id', 'role', 'role_name', 'warehouse', 'warehouse_name', 'warehouse_code',
                  'all_warehouses', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS

    def validate_role(self, value):
        if self.instance and value.pk != self.instance.role_id:
            raise serializers.ValidationError('The assigned role cannot be changed. Update its existing assignment instead.')
        return value

    def validate(self, attrs):
        if 'user' in self.initial_data:
            raise serializers.ValidationError({'user': 'Warehouse access belongs to a role. Supply the role field.'})
        return super().validate(attrs)

    @transaction.atomic
    def create(self, validated_data):
        actor = self.context['request'].user
        scope, _ = lock_assignment_write(actor, validated_data['role'].pk)
        warehouse = validated_data.get('warehouse')
        check_assignment_scope(scope, warehouse.pk if warehouse else None, validated_data.get('all_warehouses', False))
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        scope, current = lock_assignment_write(self.context['request'].user, instance.role_id, instance.pk)
        self.instance = current
        validated_data = self.validate(validated_data)
        warehouse = validated_data.get('warehouse', current.warehouse)
        check_assignment_scope(scope, warehouse.pk if warehouse else None,
                               validated_data.get('all_warehouses', current.all_warehouses))
        return super().update(current, validated_data)


class RoleWarehouseAccessFilterSerializer(serializers.Serializer):
    role = serializers.IntegerField(min_value=1, max_value=9223372036854775807, required=False)
    warehouse = serializers.IntegerField(min_value=1, max_value=9223372036854775807, required=False)
    all_warehouses = serializers.BooleanField(required=False)
