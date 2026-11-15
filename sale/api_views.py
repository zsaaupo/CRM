from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models.deletion import ProtectedError
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import TradeStatus
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView, RecordInUse
from inventory.models import Currency, Product
from people.models import Party
from warehouse.models import WareHouse

from .models import SalesOrder, SalesOrderItem
from .services import SaleBusy, complete_sale, delete_order, lock_order
from .serializers import (
    SalesOrderCreateSerializer, SalesOrderDetailSerializer, SalesOrderItemFilterSerializer,
    SalesOrderItemSerializer, SalesOrderSerializer, SaleCompleteSerializer,
)


class SalesConcurrencyMixin:
    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and ('locked' in str(exc).lower() or 'deadlock' in str(exc).lower()):
            exc = SaleBusy()
        return super().handle_exception(exc)


class SalesOrderListCreateAPIView(SalesConcurrencyMixin, BaseListCreateAPIView):
    serializer_class = SalesOrderSerializer
    queryset = SalesOrder.objects.select_related('currency', 'status', 'customer', 'warehouse').prefetch_related('items')
    response_key = 'sales_order'
    search_fields = ['order_number', 'customer__name', 'customer__code', 'customer_reference', 'notes']
    ordering = ['-order_date', '-pk']

    def get_serializer_class(self):
        return SalesOrderCreateSerializer if self.request.method == 'POST' else SalesOrderSerializer

    def perform_create(self, serializer):
        if serializer.validated_data.get('items') and not self.request.user.has_perm('sale.add_salesorderitem'):
            raise PermissionDenied('You do not have permission to add sale order items.')
        super().perform_create(serializer)


class SalesOrderListAPIView(SalesConcurrencyMixin, BaseListAPIView):
    serializer_class = SalesOrderSerializer
    queryset = SalesOrderListCreateAPIView.queryset
    search_fields = SalesOrderListCreateAPIView.search_fields
    ordering = SalesOrderListCreateAPIView.ordering


class SalesOrderDetailAPIView(SalesConcurrencyMixin, BaseDetailAPIView):
    serializer_class = SalesOrderDetailSerializer
    queryset = SalesOrderListCreateAPIView.queryset

    def get_serializer_class(self):
        return SalesOrderCreateSerializer if self.request.method in ('PUT', 'PATCH') else SalesOrderDetailSerializer

    def perform_destroy(self, instance):
        try:
            delete_order(instance, self.request)
        except ProtectedError as exc:
            raise RecordInUse() from exc


class SalesOrderUpdateAPIView(SalesConcurrencyMixin, BaseUpdateAPIView):
    serializer_class = SalesOrderCreateSerializer
    queryset = SalesOrderListCreateAPIView.queryset


class SalesOrderItemQuerysetMixin:
    search_fields = ['sales_order__order_number', 'product__name', 'product__sku', 'description']
    ordering = ['sales_order_id', 'line_number', 'pk']

    def get_queryset(self):
        queryset = super().get_queryset()
        filters = SalesOrderItemFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        order_id = filters.validated_data.get('sales_order')
        return queryset.filter(sales_order_id=order_id) if order_id is not None else queryset


class SalesOrderItemListCreateAPIView(SalesConcurrencyMixin, SalesOrderItemQuerysetMixin, BaseListCreateAPIView):
    serializer_class = SalesOrderItemSerializer
    queryset = SalesOrderItem.objects.select_related('sales_order', 'product', 'unit')
    response_key = 'sales_order_item'


class SalesOrderItemListAPIView(SalesConcurrencyMixin, SalesOrderItemQuerysetMixin, BaseListAPIView):
    serializer_class = SalesOrderItemSerializer
    queryset = SalesOrderItemListCreateAPIView.queryset


class SalesOrderItemDetailAPIView(SalesConcurrencyMixin, BaseDetailAPIView):
    serializer_class = SalesOrderItemSerializer
    queryset = SalesOrderItemListCreateAPIView.queryset

    @transaction.atomic
    def perform_destroy(self, instance):
        lock_order(instance.sales_order_id)
        super().perform_destroy(instance)


class SalesOrderItemUpdateAPIView(SalesConcurrencyMixin, BaseUpdateAPIView):
    serializer_class = SalesOrderItemSerializer
    queryset = SalesOrderItemListCreateAPIView.queryset


@method_decorator(never_cache, name='dispatch')
class SaleCompleteAPIView(SalesConcurrencyMixin, BaseAPIMixin, GenericAPIView):
    serializer_class = SaleCompleteSerializer
    required_permissions = ('sale.change_salesorder',)
    http_method_names = ['post', 'options']

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order, completed_now = complete_sale(request.user, serializer.validated_data['sale_id'])
        return Response({
            'message': 'Sale completed and stock updated.' if completed_now else 'Sale was already completed. Stock was not deducted again.',
            'completed_now': completed_now,
            'sales_order': SalesOrderDetailSerializer(order, context=self.get_serializer_context()).data,
        })


@method_decorator(never_cache, name='dispatch')
class SalesOptionsAPIView(SalesConcurrencyMixin, BaseAPIMixin, APIView):
    """Reference data for sale creation without catalog management permissions."""

    required_permissions = ('sale.add_salesorder', 'sale.add_salesorderitem')
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        return Response({
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'customer': [
                {'value': row.pk, 'label': f'{row.name} ({row.code})', 'address': row.address}
                for row in Party.objects.filter(is_customer=True).order_by('name', 'pk')
            ],
            'warehouse': [
                {'value': row.pk, 'label': row.name}
                for row in WareHouse.objects.filter(is_active=True).order_by('name', 'pk')
            ],
            'currency': [
                {'value': row.code, 'label': f'{row.name} ({row.code})',
                 'exchange_rate': str(row.exchange_rate) if row.exchange_rate is not None else None}
                for row in Currency.objects.order_by('name', 'pk')
            ],
            'status': [
                {'value': row.code, 'label': row.name}
                for row in TradeStatus.objects.order_by('name', 'pk')
            ],
            'product': [
                {'value': row.pk, 'label': f'{row.sku} — {row.name}', 'unit': row.unit_id,
                 'unit_label': f'{row.unit.name} ({row.unit.code})', 'currency': row.currency_id,
                 'sale_price': str(row.sale_price), 'tax_rate': str(row.product_tax)}
                for row in Product.objects.select_related('unit').order_by('name', 'pk')
            ],
        })


class SalesEditOptionsAPIView(SalesOptionsAPIView):
    required_permissions = ('sale.view_salesorder', 'sale.change_salesorder', 'sale.view_salesorderitem')
