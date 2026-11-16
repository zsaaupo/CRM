from common.serializers import AUDIT_FIELDS, CatalogSerializer
from django.db import transaction
from rest_framework import serializers

from .access import check_assignment_scope, lock_assignment_write
from .models import Stock, RoleWarehouseAccess, WareHouse
from .scope import WarehouseScopedSerializerMixin


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
