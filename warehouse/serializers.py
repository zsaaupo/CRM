from common.serializers import AUDIT_FIELDS, CatalogSerializer

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
