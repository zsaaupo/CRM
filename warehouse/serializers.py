from common.serializers import AUDIT_FIELDS, CatalogSerializer
from rest_framework import serializers

from .models import Stock, WareHouse


class WareHouseSerializer(CatalogSerializer):
    """Apply shared model validation and read-only audit fields."""

    class Meta:
        model = WareHouse
        fields = ['id', 'name', 'address', 'phone', 'email', 'code', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class StockSerializer(CatalogSerializer):
    class Meta:
        model = Stock
        fields = ['id', 'warehouse', 'product', 'quantity', *AUDIT_FIELDS[1:]]
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
