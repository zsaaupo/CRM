from decimal import InvalidOperation

from django.db import OperationalError, transaction
from django.db.models import Prefetch
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from inventory.models import Product
from .adjustments import AdjustmentBusy, complete_adjustment, lock_editable_adjustment
from .models import StockAdjustment, StockAdjustmentItem, WareHouse
from .scope import WarehouseScopeAPIMixin, WarehouseScopedSerializerMixin, lock_warehouse_scope, scoped_queryset, warehouse_scope


adjustment_records = StockAdjustment.objects.select_related('warehouse').prefetch_related(Prefetch(
    'items', queryset=StockAdjustmentItem.objects.select_related('product', 'unit'),
))


class StockAdjustmentLineSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False, min_value=1, max_value=9223372036854775807)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    unit_code = serializers.CharField(source='unit.code', read_only=True)

    class Meta:
        model = StockAdjustmentItem
        fields = ['id', 'line_number', 'product', 'product_name', 'product_sku', 'unit', 'unit_code', 'operation', 'quantity']
        read_only_fields = ['line_number', 'unit']

    def validate(self, attrs):
        # Including items in a PATCH replaces the line selection, so each supplied line is complete.
        missing = {'product', 'operation', 'quantity'} - attrs.keys()
        if missing:
            raise serializers.ValidationError({field: 'This field is required.' for field in sorted(missing)})
        return attrs


class StockAdjustmentSerializer(WarehouseScopedSerializerMixin, CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    items = StockAdjustmentLineSerializer(many=True, required=False, allow_empty=False)
    item_count = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = StockAdjustment
        fields = ['id', 'adjustment_number', 'adjustment_date', 'warehouse', 'warehouse_name', 'notes',
                  'is_completed', 'status_label', 'completed_at', 'item_count', 'items', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'adjustment_number', 'is_completed', 'completed_at']
        extra_kwargs = {'warehouse': {'required': False}}

    def get_item_count(self, record):
        return len(record.items.all())

    def get_status_label(self, record):
        return 'Completed' if record.is_completed else 'Draft'

    def validate_items(self, items):
        ids = [item['id'] for item in items if 'id' in item]
        if self.instance is None and ids:
            raise serializers.ValidationError('New adjustment lines must not include existing IDs.')
        if len(set(ids)) != len(ids):
            raise serializers.ValidationError('Line IDs must be unique within the adjustment.')
        return items

    def validate(self, attrs):
        items = attrs.pop('items', None)
        if self.instance is None and items is None:
            raise serializers.ValidationError({'items': 'Add at least one product.'})
        attrs = super().validate(attrs)
        warehouse = attrs.get('warehouse', getattr(self.instance, 'warehouse', None))
        if warehouse and not warehouse.is_active and warehouse.pk != getattr(self.instance, 'warehouse_id', None):
            raise serializers.ValidationError({'warehouse': 'Choose an active warehouse.'})
        if items is not None:
            attrs['items'] = items
        return attrs

    @staticmethod
    def save_items(record, items, username):
        existing = {item.pk: item for item in record.items.all()}
        retained = {item['id'] for item in items if 'id' in item}
        if not retained.issubset(existing):
            raise serializers.ValidationError({'items': 'Every line ID must belong to this adjustment.'})
        record.items.filter(pk__in=existing.keys() - retained).delete()
        occupied = {item.line_number for item in existing.values()}
        temporary_number = len(items)
        # Stage moved lines before assigning their final positions so swaps keep their IDs and audit history.
        for number, item in enumerate(items, start=1):
            line = existing.get(item.get('id'))
            if line and line.line_number != number:
                temporary_number += 1
                while temporary_number in occupied:
                    temporary_number += 1
                record.items.filter(pk=line.pk).update(line_number=temporary_number)
        for number, item in enumerate(items, start=1):
            product = item['product']
            values = {'line_number': number, 'product_id': product.pk, 'unit_id': product.unit_id,
                      'operation': item['operation'], 'quantity': item['quantity']}
            line = existing.get(item.get('id'))
            if line is None:
                record.items.create(**values, create_by=username, modified_by=username)
            elif any(getattr(line, name) != value for name, value in values.items()):
                for name, value in values.items():
                    setattr(line, name, value)
                line.modified_by = username
                line.save(update_fields=[*values, 'modified_by', 'updated_at'])

    @staticmethod
    def saved_record(record, items):
        message = 'The database cannot store one or more adjustment quantities accurately.'
        try:
            saved = adjustment_records.get(pk=record.pk)
        except InvalidOperation as exc:
            raise serializers.ValidationError({'items': message}) from exc
        if items is not None and any(line.quantity != item['quantity'] for line, item in zip(saved.items.all(), items, strict=True)):
            raise serializers.ValidationError({'items': message})
        return saved

    def create(self, validated_data):
        items = validated_data.pop('items')
        record = super().create(validated_data)
        self.save_items(record, items, record.create_by)
        return self.saved_record(record, items)

    def update(self, instance, validated_data):
        actor = self.context['request'].user
        self.instance = lock_editable_adjustment(instance.pk, warehouse_scope(actor))
        validated_data = self.validate(validated_data)
        items = validated_data.pop('items', None)
        record = super().update(self.instance, validated_data)
        if items is not None:
            self.save_items(record, items, actor.get_username())
        return self.saved_record(record, items)


class StockAdjustmentAPIMixin(WarehouseScopeAPIMixin):
    queryset = adjustment_records
    serializer_class = StockAdjustmentSerializer
    response_key = 'stock_adjustment'
    ordering = ['-adjustment_date', '-pk']
    search_fields = ['adjustment_number', 'warehouse__name', 'notes', 'items__product__name', 'items__product__sku']

    def get_queryset(self):
        return super().get_queryset().distinct()

    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and ('locked' in str(exc).lower() or 'deadlock' in str(exc).lower()):
            exc = AdjustmentBusy()
        return super().handle_exception(exc)


class StockAdjustmentListCreateAPIView(StockAdjustmentAPIMixin, BaseListCreateAPIView):
    pass


class StockAdjustmentListAPIView(StockAdjustmentAPIMixin, BaseListAPIView):
    pass


class StockAdjustmentDetailAPIView(StockAdjustmentAPIMixin, BaseDetailAPIView):
    @transaction.atomic
    def perform_destroy(self, instance):
        scope = lock_warehouse_scope(self.request.user)
        self.check_permissions(self.request)
        lock_editable_adjustment(instance.pk, scope).delete()


class StockAdjustmentUpdateAPIView(StockAdjustmentAPIMixin, BaseUpdateAPIView):
    pass


class AdjustmentCompleteSerializer(serializers.Serializer):
    adjustment_id = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    is_completed = serializers.BooleanField(default=True)

    def validate_is_completed(self, value):
        if not value:
            raise serializers.ValidationError('Set is_completed to true to complete the adjustment.')
        return value


@method_decorator(never_cache, name='dispatch')
class AdjustmentCompleteAPIView(StockAdjustmentAPIMixin, BaseAPIMixin, GenericAPIView):
    serializer_class = AdjustmentCompleteSerializer
    required_permissions = ('warehouse.change_stockadjustment',)
    http_method_names = ['post', 'options']

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record, completed_now = complete_adjustment(request.user, serializer.validated_data['adjustment_id'])
        record = adjustment_records.get(pk=record.pk)
        return Response({
            'message': 'Adjustment completed and stock updated.' if completed_now else 'This adjustment is already completed.',
            'completed_now': completed_now,
            'stock_adjustment': StockAdjustmentSerializer(record, context=self.get_serializer_context()).data,
        })


@method_decorator(never_cache, name='dispatch')
class AdjustmentOptionsAPIView(StockAdjustmentAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.add_stockadjustment',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        warehouses = scoped_queryset(WareHouse.objects.filter(is_active=True), request.warehouse_scope, 'pk')
        products = Product.objects.select_related('unit').order_by('name', 'pk')
        return Response({
            'warehouse': [{'value': row.pk, 'label': str(row)} for row in warehouses.order_by('name', 'pk')],
            'product': [{'value': row.pk, 'label': str(row), 'unit_label': str(row.unit)} for row in products],
        })


class AdjustmentEditOptionsAPIView(AdjustmentOptionsAPIView):
    required_permissions = ('warehouse.view_stockadjustment', 'warehouse.change_stockadjustment')

