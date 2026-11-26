from copy import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from common.serializers import AUDIT_FIELDS
from inventory.models import RAW_MATERIAL_TYPE, ProductType
from inventory.serializers import ProductSerializer
from warehouse.scope import WarehouseScopedSerializerMixin

from .models import RawMaterial
from .services import lock_raw_material


class RawMaterialProductSerializer(ProductSerializer):
    product_type = serializers.CharField(source='product_type_id', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)

    class Meta(ProductSerializer.Meta):
        fields = ['public_id', 'sku', 'name', 'product_type', 'status', 'unit', 'unit_code', 'currency',
                  'category', 'supplier', 'cost_price', 'description', 'attributes', 'barcode']


class RawMaterialSerializer(WarehouseScopedSerializerMixin, serializers.ModelSerializer):
    id = serializers.IntegerField(source='pk', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, read_only=True)
    low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = RawMaterial
        fields = ['id', 'warehouse', 'warehouse_name', 'reorder_level', 'quantity', 'low_stock', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS
        extra_kwargs = {'warehouse': {'required': False}}

    def get_fields(self):
        fields = super().get_fields()
        # Keep product fields flat in the API and reuse their catalog validation.
        for name, field in RawMaterialProductSerializer().get_fields().items():
            field.source = f'product.{field.source or name}'
            fields[name] = field
        return fields

    def validate(self, attrs):
        attrs = super().validate(attrs)
        material = copy(self.instance) if self.instance else RawMaterial()
        if 'warehouse' in attrs:
            material.warehouse = attrs['warehouse']
        try:
            material.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc

        product_data = dict(attrs.get('product', {}))
        if self.instance is None:
            product_data['product_type'] = ProductType.objects.get(code=RAW_MATERIAL_TYPE)
        attrs['product'] = ProductSerializer(self.instance.product if self.instance else None).validate(product_data)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        product_data = validated_data.pop('product')
        product = ProductSerializer().create({**product_data, 'create_by': validated_data.get('create_by'),
                                              'modified_by': validated_data.get('modified_by')})
        return super().create({**validated_data, 'product': product})

    @transaction.atomic
    def update(self, instance, validated_data):
        self.instance = lock_raw_material(instance.pk, self.context['request'].user)
        validated_data = self.validate(validated_data)
        product_data = validated_data.pop('product')
        ProductSerializer().update(self.instance.product, {**product_data, 'modified_by': validated_data.get('modified_by')})
        return super().update(self.instance, validated_data)


class RawMaterialDetailSerializer(RawMaterialSerializer):
    status_name = serializers.CharField(source='product.status.name', read_only=True)
    category_name = serializers.CharField(source='product.category.name', read_only=True, default=None)
    supplier_name = serializers.CharField(source='product.supplier.name', read_only=True, default=None)
    unit_name = serializers.CharField(source='product.unit.name', read_only=True)

    class Meta(RawMaterialSerializer.Meta):
        fields = [*RawMaterialSerializer.Meta.fields, 'status_name', 'category_name', 'supplier_name', 'unit_name']
