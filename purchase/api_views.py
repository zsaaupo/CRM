from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Status
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from inventory.models import Currency, Product
from people.models import Party

from .models import PurchaseOrder, PurchaseOrderItem
from .serializers import (
    PurchaseOrderCreateSerializer, PurchaseOrderDetailSerializer, PurchaseOrderItemFilterSerializer,
    PurchaseOrderItemSerializer, PurchaseOrderSerializer,
)


class PurchaseOrderListCreateAPIView(BaseListCreateAPIView):
    serializer_class = PurchaseOrderSerializer
    queryset = PurchaseOrder.objects.select_related('currency', 'status', 'supplier').prefetch_related('items')
    response_key = 'purchase_order'
    search_fields = ['order_number', 'supplier__name', 'supplier__code', 'supplier_reference', 'notes']
    ordering = ['-order_date', '-pk']

    def get_serializer_class(self):
        return PurchaseOrderCreateSerializer if self.request.method == 'POST' else PurchaseOrderSerializer

    def perform_create(self, serializer):
        if serializer.validated_data.get('items') and not self.request.user.has_perm('purchase.add_purchaseorderitem'):
            raise PermissionDenied('You do not have permission to add purchase order items.')
        super().perform_create(serializer)


class PurchaseOrderListAPIView(BaseListAPIView):
    serializer_class = PurchaseOrderSerializer
    queryset = PurchaseOrderListCreateAPIView.queryset
    search_fields = PurchaseOrderListCreateAPIView.search_fields
    ordering = PurchaseOrderListCreateAPIView.ordering


class PurchaseOrderDetailAPIView(BaseDetailAPIView):
    serializer_class = PurchaseOrderDetailSerializer
    queryset = PurchaseOrderListCreateAPIView.queryset


class PurchaseOrderUpdateAPIView(BaseUpdateAPIView):
    serializer_class = PurchaseOrderSerializer
    queryset = PurchaseOrderListCreateAPIView.queryset


class PurchaseOrderItemQuerysetMixin:
    search_fields = ['purchase_order__order_number', 'product__name', 'product__sku', 'description']
    ordering = ['purchase_order_id', 'line_number', 'pk']

    def get_queryset(self):
        queryset = super().get_queryset()
        filters = PurchaseOrderItemFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        order_id = filters.validated_data.get('purchase_order')
        return queryset.filter(purchase_order_id=order_id) if order_id is not None else queryset


class PurchaseOrderItemListCreateAPIView(PurchaseOrderItemQuerysetMixin, BaseListCreateAPIView):
    serializer_class = PurchaseOrderItemSerializer
    queryset = PurchaseOrderItem.objects.select_related('purchase_order', 'product', 'unit')
    response_key = 'purchase_order_item'


class PurchaseOrderItemListAPIView(PurchaseOrderItemQuerysetMixin, BaseListAPIView):
    serializer_class = PurchaseOrderItemSerializer
    queryset = PurchaseOrderItemListCreateAPIView.queryset


class PurchaseOrderItemDetailAPIView(BaseDetailAPIView):
    serializer_class = PurchaseOrderItemSerializer
    queryset = PurchaseOrderItemListCreateAPIView.queryset


class PurchaseOrderItemUpdateAPIView(BaseUpdateAPIView):
    serializer_class = PurchaseOrderItemSerializer
    queryset = PurchaseOrderItemListCreateAPIView.queryset


@method_decorator(never_cache, name='dispatch')
class PurchaseOptionsAPIView(BaseAPIMixin, APIView):
    """Reference data for purchase creation without catalog management permissions."""

    required_permissions = ('purchase.add_purchaseorder', 'purchase.add_purchaseorderitem')
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        return Response({
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'supplier': [
                {'value': row.pk, 'label': f'{row.name} ({row.code})'}
                for row in Party.objects.filter(is_supplier=True).order_by('name', 'pk')
            ],
            'currency': [
                {'value': row.code, 'label': f'{row.name} ({row.code})',
                 'exchange_rate': str(row.exchange_rate) if row.exchange_rate is not None else None}
                for row in Currency.objects.order_by('name', 'pk')
            ],
            'status': [
                {'value': row.code, 'label': row.name}
                for row in Status.objects.order_by('name', 'pk')
            ],
            'product': [
                {'value': row.pk, 'label': f'{row.sku} — {row.name}', 'unit': row.unit_id,
                 'unit_label': f'{row.unit.name} ({row.unit.code})'}
                for row in Product.objects.select_related('unit').order_by('name', 'pk')
            ],
        })
