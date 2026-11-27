from decimal import Decimal

from django.db import OperationalError, transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from account.permissions import has_role_permissions
from accounting.models import ZERO, base_currency
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from warehouse.scope import HasWarehouseAccess, lock_warehouse_scope, warehouse_scope

from .models import PurchaseReturn, PurchaseReturnItem
from .returns import (
    RETURN_CREATE, RETURN_EDIT, RETURN_WRITE, SOURCE_PERMISSIONS, ReturnBusy, ReturnCompleted, complete_return,
    lock_return, received_orders, require_permissions, returned_quantities, scoped_returns, validate_items,
)


class PurchaseReturnSerializer(CatalogSerializer):
    purchase_order_number = serializers.CharField(source='purchase_order.order_number', read_only=True)
    warehouse_name = serializers.CharField(source='purchase_order.warehouse.name', read_only=True, default=None)
    supplier_name = serializers.CharField(source='purchase_order.supplier.name', read_only=True, default=None)

    class Meta:
        model = PurchaseReturn
        fields = ['id', 'return_number', 'purchase_order', 'purchase_order_number', 'warehouse_name', 'supplier_name',
                  'return_date', 'notes', 'is_completed', 'completed_at', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'return_number', 'is_completed', 'completed_at']


class PurchaseReturnLineSerializer(serializers.ModelSerializer):
    line_number = serializers.IntegerField(source='purchase_order_item.line_number')
    product_name = serializers.CharField(source='purchase_order_item.product.name')
    product_sku = serializers.CharField(source='purchase_order_item.product.sku')
    unit_code = serializers.CharField(source='purchase_order_item.unit.code')

    class Meta:
        model = PurchaseReturnItem
        fields = ['id', 'purchase_order_item', 'line_number', 'product_name', 'product_sku', 'unit_code', 'quantity']


class PurchaseReturnDetailSerializer(PurchaseReturnSerializer):
    items = PurchaseReturnLineSerializer(many=True, read_only=True)
    refund_amount = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()

    class Meta(PurchaseReturnSerializer.Meta):
        fields = [*PurchaseReturnSerializer.Meta.fields, 'items', 'refund_amount', 'currency']

    def get_refund_amount(self, record):
        if not record.is_completed:
            return None
        return format(sum((p.amount for p in record.refund_payments.all()), ZERO), '.4f')

    def get_currency(self, record):
        return base_currency()

    def get_fields(self):
        fields = super().get_fields()
        if not has_role_permissions(self.context['request'].user, ('purchase.view_purchasereturnitem',)):
            fields.pop('items')
        return fields


class ReturnItemInputSerializer(serializers.Serializer):
    purchase_order_item = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal('0.000001'))


class PurchaseReturnWriteSerializer(PurchaseReturnSerializer):
    items = ReturnItemInputSerializer(many=True, required=False, allow_empty=False, write_only=True)

    class Meta(PurchaseReturnSerializer.Meta):
        fields = [*PurchaseReturnSerializer.Meta.fields, 'items']

    def get_fields(self):
        fields = super().get_fields()
        user = self.context['request'].user
        orders = received_orders(warehouse_scope(user))
        fields['purchase_order'].queryset = orders.filter(pk=self.instance.purchase_order_id) if self.instance else orders
        return fields

    def validate(self, attrs):
        if self.instance and self.instance.is_completed:
            raise ReturnCompleted()
        items = attrs.pop('items', None)
        user = self.context['request'].user
        order = attrs.get('purchase_order', self.instance.purchase_order if self.instance else None)
        if self.instance is None or items is not None:
            require_permissions(user, *SOURCE_PERMISSIONS)
        attrs = super().validate(attrs)
        if order and not order.warehouse.is_active:
            raise serializers.ValidationError({'purchase_order': 'Choose a received purchase from an active warehouse.'})
        if self.instance is None and items is None:
            raise serializers.ValidationError({'items': 'Add at least one return quantity.'})
        if items is not None:
            quantities = {line['purchase_order_item']: line['quantity'] for line in items}
            if len(quantities) != len(items):
                raise serializers.ValidationError({'items': 'Each original purchase line may appear once per return.'})
            validate_items(order, quantities)
            attrs['items'] = items
        return attrs

    @staticmethod
    def check_stored_quantities(record, expected):
        if dict(record.items.values_list('purchase_order_item_id', 'quantity')) != expected:
            raise serializers.ValidationError({'items': 'The database could not store the entered return quantities exactly.'})

    @transaction.atomic
    def create(self, validated_data):
        user = self.context['request'].user
        scope = lock_warehouse_scope(user)
        require_permissions(user, *RETURN_WRITE)
        order = received_orders(scope).filter(pk=validated_data['purchase_order'].pk).first()
        if order is None:
            raise serializers.ValidationError({'purchase_order': 'Choose a received purchase in your warehouse scope.'})
        validated_data['purchase_order'] = order
        values = self.validate(validated_data)
        items = values.pop('items')
        record = super().create(values)
        PurchaseReturnItem.objects.bulk_create([
            PurchaseReturnItem(purchase_return=record, purchase_order_item_id=item['purchase_order_item'], quantity=item['quantity'],
                            create_by=record.create_by, modified_by=record.modified_by) for item in items
        ])
        self.check_stored_quantities(record, {item['purchase_order_item']: item['quantity'] for item in items})
        return record

    @transaction.atomic
    def update(self, instance, validated_data):
        user = self.context['request'].user
        self.instance = current = lock_return(instance.pk, user)
        values = self.validate(validated_data)
        items = values.pop('items', None)
        if items is not None:
            expected = {row['purchase_order_item']: row['quantity'] for row in items}
            existing = {row.purchase_order_item_id: row for row in current.items.all()}
            added = expected.keys() - existing.keys()
            removed = existing.keys() - expected.keys()
            changed = {key for key in expected.keys() & existing.keys() if expected[key] != existing[key].quantity}
            permissions = tuple(f'purchase.{action}_purchasereturnitem' for action, rows in (
                ('add', added), ('change', changed), ('delete', removed),
            ) if rows)
            if permissions:
                require_permissions(user, *permissions)
            actor = user.get_username()
            if removed:
                current.items.filter(purchase_order_item_id__in=removed).delete()
            if added:
                PurchaseReturnItem.objects.bulk_create([
                    PurchaseReturnItem(purchase_return=current, purchase_order_item_id=key, quantity=expected[key],
                                    create_by=actor, modified_by=actor) for key in added
                ])
            if changed:
                now = timezone.now()
                for key in changed:
                    line = existing[key]
                    line.quantity = expected[key]
                    line.modified_by = actor
                    line.updated_at = now
                PurchaseReturnItem.objects.bulk_update([existing[key] for key in changed], ['quantity', 'modified_by', 'updated_at'])
            self.check_stored_quantities(current, expected)
        return super().update(current, values)


class PurchaseReturnResource:
    role_permissions_only = True
    queryset = PurchaseReturn.objects.select_related('purchase_order__warehouse', 'purchase_order__supplier')
    search_fields = ['return_number', 'purchase_order__order_number', 'purchase_order__supplier__name', 'notes']
    ordering = ['-return_date', '-pk']

    def get_permissions(self):
        return [*super().get_permissions(), HasWarehouseAccess()]

    def get_queryset(self):
        return scoped_returns(super().get_queryset(), self.request.warehouse_scope)

    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and any(word in str(exc).lower() for word in ('locked', 'deadlock')):
            exc = ReturnBusy()
        return super().handle_exception(exc)


class PurchaseReturnListCreateAPIView(PurchaseReturnResource, BaseListCreateAPIView):
    response_key = 'purchase_return'

    @property
    def required_permissions(self):
        return {**super().required_permissions, 'POST': RETURN_WRITE}

    def get_serializer_class(self):
        return PurchaseReturnWriteSerializer if self.request.method == 'POST' else PurchaseReturnSerializer


class PurchaseReturnListAPIView(PurchaseReturnResource, BaseListAPIView):
    serializer_class = PurchaseReturnSerializer


class PurchaseReturnDetailAPIView(PurchaseReturnResource, BaseDetailAPIView):
    def get_serializer_class(self):
        return PurchaseReturnWriteSerializer if self.request.method in ('PATCH', 'PUT') else PurchaseReturnDetailSerializer

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related('refund_payments')
        if has_role_permissions(self.request.user, ('purchase.view_purchasereturnitem',)):
            return queryset.prefetch_related(Prefetch('items', queryset=PurchaseReturnItem.objects.select_related(
                'purchase_order_item__unit', 'purchase_order_item__product')))
        return queryset

    @transaction.atomic
    def perform_destroy(self, instance):
        current = lock_return(instance.pk, self.request.user, 'delete')
        if current.items.exists():
            require_permissions(self.request.user, 'purchase.delete_purchasereturnitem')
            current.items.all().delete()
        super().perform_destroy(current)


class PurchaseReturnUpdateAPIView(PurchaseReturnResource, BaseUpdateAPIView):
    serializer_class = PurchaseReturnWriteSerializer


class ReturnCompleteSerializer(serializers.Serializer):
    return_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
    is_completed = serializers.BooleanField()

    def validate_is_completed(self, value):
        if not value:
            raise serializers.ValidationError('This field must be true to complete the return.')
        return value


@method_decorator(never_cache, name='dispatch')
class PurchaseReturnCompleteAPIView(PurchaseReturnResource, BaseAPIMixin, APIView):
    required_permissions = ('purchase.change_purchasereturn',)
    http_method_names = ['post', 'options']

    def post(self, request):
        serializer = ReturnCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record, completed_now = complete_return(request.user, serializer.validated_data['return_id'])
        return Response({
            'message': 'Return completed and stock deducted.' if completed_now else 'Return was already completed.',
            'completed_now': completed_now, 'purchase_return': PurchaseReturnSerializer(record).data,
        })


@method_decorator(never_cache, name='dispatch')
class PurchaseReturnOptionsAPIView(PurchaseReturnResource, BaseAPIMixin, APIView):
    http_method_names = ['get', 'head', 'options']

    @property
    def required_permissions(self):
        return RETURN_EDIT if 'pk' in self.kwargs else RETURN_CREATE

    def get(self, request, pk=None):
        orders = received_orders(request.warehouse_scope).select_related('warehouse', 'supplier')
        if pk is not None:
            record = get_object_or_404(scoped_returns(self.queryset, request.warehouse_scope), pk=pk)
            order = record.purchase_order
        elif 'purchase_order' in request.query_params:
            field = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
            try:
                order_id = field.run_validation(request.query_params['purchase_order'])
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({'purchase_order': exc.detail}) from exc
            order = get_object_or_404(orders.filter(warehouse__is_active=True), pk=order_id)
        else:
            return Response({'orders': [self.order_option(row) for row in orders.filter(warehouse__is_active=True)]})

        returned = returned_quantities(order)
        lines = []
        for line in order.items.select_related('product', 'unit'):
            received = line.received_quantity if order.receipts_started else line.quantity
            remaining = max(received - returned[line.pk], Decimal('0'))
            lines.append({'id': line.pk, 'line_number': line.line_number, 'product_name': line.product.name,
                          'product_sku': line.product.sku, 'unit_code': line.unit.code, 'received_quantity': str(received),
                          'returned_quantity': str(returned[line.pk]), 'remaining_quantity': str(remaining),
                          'returnable': order.warehouse.is_active and line.product.is_returnable and line.product.unit_id == line.unit_id})
        return Response({'order': self.order_option(order), 'lines': lines})

    @staticmethod
    def order_option(order):
        label = f'{order.order_number} ({order.order_date})'
        if order.supplier_id:
            label = f'{label} · {order.supplier.name}'
        return {'value': order.pk, 'label': label, 'warehouse': order.warehouse.name, 'order_date': order.order_date}
