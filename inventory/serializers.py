from common.models import ProductStatus
from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from .models import (
    Brand, Currency, Product, ProductCategory, ProductComboItem,
    ProductType, TaxRule, UnitOfMeasure, WarehouseProductPrice,
)


class ProductCategorySerializer(CatalogSerializer):
    class Meta:
        model = ProductCategory
        fields = ['id', 'name', 'code', 'description', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class BrandSerializer(CatalogSerializer):
    class Meta:
        model = Brand
        fields = ['id', 'name', 'website', 'description', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class UnitOfMeasureSerializer(CatalogSerializer):
    factor = serializers.DecimalField(max_digits=18, decimal_places=6, read_only=True)

    class Meta:
        model = UnitOfMeasure
        fields = ['id', 'name', 'code', 'base_unit', 'operator', 'operation_value', 'factor', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'factor']


class TaxRuleSerializer(CatalogSerializer):
    class Meta:
        model = TaxRule
        fields = ['id', 'name', 'rate', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class ProductComboItemSerializer(CatalogSerializer):
    component_sku = serializers.CharField(source='component_product.sku', read_only=True)
    component_name = serializers.CharField(source='component_product.name', read_only=True)

    class Meta:
        model = ProductComboItem
        fields = ['id', 'combo_product', 'component_product', 'component_sku', 'component_name',
                  'quantity', 'unit_price', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class WarehouseProductPriceSerializer(CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)

    class Meta:
        model = WarehouseProductPrice
        fields = ['id', 'warehouse', 'warehouse_name', 'product', 'sale_price', 'wholesale_price',
                  'alert_quantity', 'effective_from', 'effective_to', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class ProductLookupSerializer(CatalogSerializer):
    def validate_code(self, value):
        if self.instance and value != self.instance.code:
            raise serializers.ValidationError('The code cannot be changed after creation.')
        return value


class ProductTypeSerializer(ProductLookupSerializer):
    class Meta:
        model = ProductType
        fields = ['id', 'code', 'name', 'description', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class StatusSerializer(ProductLookupSerializer):
    class Meta:
        model = ProductStatus
        fields = ['id', 'code', 'name', 'description', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class CurrencySerializer(ProductLookupSerializer):
    base_currency = serializers.SerializerMethodField()
    exchange_rate = serializers.DecimalField(max_digits=20, decimal_places=8,
                                             min_value=Decimal('0.00000001'))

    def get_base_currency(self, obj):
        return settings.INVENTORY_BASE_CURRENCY

    class Meta:
        model = Currency
        fields = ['id', 'code', 'name', 'symbol', 'exchange_rate', 'base_currency', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class ProductSerializer(CatalogSerializer):
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all())
    product_type = serializers.SlugRelatedField(slug_field='code', queryset=ProductType.objects.all(), required=False)
    status = serializers.SlugRelatedField(slug_field='code', queryset=ProductStatus.objects.all(), required=False)
    image = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = Product
        fields = [
            'id', 'public_id', 'sku', 'name', 'barcode', 'barcode_symbology', 'embedded_barcode',
            'product_type', 'supply_method', 'status', 'category', 'brand', 'unit', 'sale_unit',
            'purchase_unit', 'variant_of', 'description', 'image', 'manufacturer',
            'manufacturer_part_number', 'attributes', 'currency', 'cost_price', 'sale_price',
            'minimum_sale_price', 'Whole_sale_price', 'product_tax', 'tax_rule', 'tax_method',
            'alert_quantity', 'daily_sale_objective', 'is_featured', 'has_batch_expiry',
            'has_imei_serial', 'is_promo', 'promo_price', 'promo_start_date', 'promo_end_date',
            'is_returnable', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'public_id']


class ProductDetailSerializer(ProductSerializer):
    product_type_name = serializers.CharField(source='product_type.name', read_only=True)
    status_name = serializers.CharField(source='status.name', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default=None)
    brand_name = serializers.CharField(source='brand.name', read_only=True, default=None)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)
    currency_name = serializers.CharField(source='currency.name', read_only=True)
    variant_of_name = serializers.CharField(source='variant_of.name', read_only=True, default=None)
    variant_of_sku = serializers.CharField(source='variant_of.sku', read_only=True, default=None)

    class Meta(ProductSerializer.Meta):
        fields = [*ProductSerializer.Meta.fields, 'product_type_name', 'status_name', 'category_name',
                  'brand_name', 'unit_name', 'unit_code', 'currency_name', 'variant_of_name', 'variant_of_sku']
