from copy import copy
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from rest_framework import serializers

from .models import Brand, Currency, Product, ProductCategory, ProductType, Status, UnitOfMeasure


AUDIT_FIELDS = ['id', 'created_at', 'updated_at', 'create_by', 'modified_by']


class CatalogSerializer(serializers.ModelSerializer):
    """Run model business rules for both new records and merged partial updates."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        candidate = copy(self.instance) if self.instance else self.Meta.model()
        for name, value in attrs.items():
            setattr(candidate, name, value)
        try:
            candidate.full_clean(exclude=['created_at', 'updated_at', 'create_by', 'modified_by'])
        except DjangoValidationError as exc:
            errors = exc.message_dict if hasattr(exc, 'message_dict') else {'__all__': exc.messages}
            raise serializers.ValidationError({
                'non_field_errors' if name == '__all__' else name: messages
                for name, messages in errors.items()
            }) from exc
        for name in ('sku', 'name'):
            if name in attrs:
                attrs[name] = getattr(candidate, name)
        return attrs


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
    class Meta:
        model = UnitOfMeasure
        fields = ['id', 'name', 'code', 'is_active', *AUDIT_FIELDS[1:]]
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
        model = Status
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
    status = serializers.SlugRelatedField(slug_field='code', queryset=Status.objects.all(), required=False)
    image = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = Product
        fields = [
            'id', 'public_id', 'sku', 'name', 'barcode', 'product_type', 'status',
            'category', 'brand', 'unit', 'variant_of', 'description', 'image',
            'manufacturer', 'manufacturer_part_number', 'attributes', 'currency',
            'cost_price', 'sale_price', 'minimum_sale_price', 'Whole_sale_price',
            'product_tax', 'is_returnable', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'public_id']
