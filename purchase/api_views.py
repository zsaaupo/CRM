from django.conf import settings
from django.db import OperationalError
from django.db.models import OuterRef, Subquery
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import GenericAPIView

from accounting.models import PurchasePayment
from accounting.services import available_payment_accounts, payment_account_options
from common.models import TradeStatus
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from inventory.models import Currency, Product
from people.models import Party
from warehouse.models import WareHouse
from warehouse.scope import WarehouseScopeAPIMixin, scoped_queryset, warehouse_scope, warehouse_scope_options
from account.permissions import has_role_permissions

from .models import PurchaseOrder, PurchaseOrderItem
from .services import PurchaseBusy, PurchaseDeleteGuardMixin, receive_purchase
from .serializers import (
    PurchaseOrderCreateSerializer, PurchaseOrderDetailSerializer, PurchaseOrderItemFilterSerializer,
    PurchaseOrderItemSerializer, PurchaseOrderSerializer, PurchaseReceiveSerializer,
)


class PurchaseConcurrencyMixin:
    def handle_exception(self, exc):
        # Validation and object lookup may read while SQLite's writer is active,
        # before the serializer reaches the guarded save operation.
        if isinstance(exc, OperationalError) and ('locked' in str(exc).lower() or 'deadlock' in str(exc).lower()):
            exc = PurchaseBusy()
        return super().handle_exception(exc)


class PurchaseOrderListCreateAPIView(PurchaseConcurrencyMixin, WarehouseScopeAPIMixin, BaseListCreateAPIView):
    serializer_class = PurchaseOrderSerializer
    queryset = PurchaseOrder.objects.select_related(
        'currency', 'status', 'supplier', 'warehouse', 'payment_account',
    ).prefetch_related('items')
    response_key = 'purchase_order'
    search_fields = ['order_number', 'supplier__name', 'supplier__code', 'supplier_reference', 'notes']
    ordering = ['-order_date', '-pk']

    def get_serializer_class(self):
        return PurchaseOrderCreateSerializer if self.request.method == 'POST' else PurchaseOrderSerializer

    def perform_create(self, serializer):
        if serializer.validated_data.get('items') and not has_role_permissions(self.request.user, ('purchase.add_purchaseorderitem',)):
            raise PermissionDenied('You do not have permission to add purchase order items.')
        super().perform_create(serializer)


class PurchaseOrderListAPIView(PurchaseConcurrencyMixin, WarehouseScopeAPIMixin, BaseListAPIView):
    serializer_class = PurchaseOrderSerializer
    queryset = PurchaseOrderListCreateAPIView.queryset
    search_fields = PurchaseOrderListCreateAPIView.search_fields
    ordering = PurchaseOrderListCreateAPIView.ordering


class PurchaseOrderDetailAPIView(PurchaseConcurrencyMixin, WarehouseScopeAPIMixin, PurchaseDeleteGuardMixin, BaseDetailAPIView):
    serializer_class = PurchaseOrderDetailSerializer
    queryset = PurchaseOrderListCreateAPIView.queryset.annotate(posted_payment_amount=Subquery(
        PurchasePayment.objects.filter(purchase_order_id=OuterRef('pk'), purchase_return__isnull=True)
        .order_by().values('amount')[:1],
    ))

    def get_serializer_class(self):
        return PurchaseOrderCreateSerializer if self.request.method in ('PUT', 'PATCH') else PurchaseOrderDetailSerializer


class PurchaseOrderUpdateAPIView(PurchaseConcurrencyMixin, WarehouseScopeAPIMixin, BaseUpdateAPIView):
    serializer_class = PurchaseOrderCreateSerializer
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


class PurchaseOrderItemListCreateAPIView(PurchaseConcurrencyMixin, PurchaseOrderItemQuerysetMixin, WarehouseScopeAPIMixin, BaseListCreateAPIView):
    warehouse_lookup = "purchase_order__warehouse_id"
    serializer_class = PurchaseOrderItemSerializer
    queryset = PurchaseOrderItem.objects.select_related('purchase_order', 'product', 'unit')
    response_key = 'purchase_order_item'


class PurchaseOrderItemListAPIView(PurchaseConcurrencyMixin, PurchaseOrderItemQuerysetMixin, WarehouseScopeAPIMixin, BaseListAPIView):
    warehouse_lookup = "purchase_order__warehouse_id"
    serializer_class = PurchaseOrderItemSerializer
    queryset = PurchaseOrderItemListCreateAPIView.queryset


class PurchaseOrderItemDetailAPIView(PurchaseConcurrencyMixin, WarehouseScopeAPIMixin, PurchaseDeleteGuardMixin, BaseDetailAPIView):
    warehouse_lookup = "purchase_order__warehouse_id"
    serializer_class = PurchaseOrderItemSerializer
    queryset = PurchaseOrderItemListCreateAPIView.queryset


class PurchaseOrderItemUpdateAPIView(PurchaseConcurrencyMixin, WarehouseScopeAPIMixin, BaseUpdateAPIView):
    warehouse_lookup = "purchase_order__warehouse_id"
    serializer_class = PurchaseOrderItemSerializer
    queryset = PurchaseOrderItemListCreateAPIView.queryset


@method_decorator(never_cache, name='dispatch')
class PurchaseReceiveAPIView(PurchaseConcurrencyMixin, WarehouseScopeAPIMixin, BaseAPIMixin, GenericAPIView):
    serializer_class = PurchaseReceiveSerializer
    required_permissions = ('purchase.change_purchaseorder',)
    http_method_names = ['post', 'options']

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order, received_now = receive_purchase(request.user, serializer.validated_data['purchase_id'])
        return Response({
            'message': 'Purchase received and stock updated.' if received_now else 'Purchase was already received. Stock was not added again.',
            'received_now': received_now,
            'purchase_order': PurchaseOrderDetailSerializer(order, context=self.get_serializer_context()).data,
        })


@method_decorator(never_cache, name='dispatch')
class PurchaseOptionsAPIView(PurchaseConcurrencyMixin, WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    """Reference data for purchase creation without catalog management permissions."""

    required_permissions = ('purchase.add_purchaseorder', 'purchase.add_purchaseorderitem')
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        return Response({
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'warehouse_scope': warehouse_scope_options(request.user),
            'supplier': [
                {'value': row.pk, 'label': f'{row.name} ({row.code})'}
                for row in Party.objects.filter(is_supplier=True).order_by('name', 'pk')
            ],
            'warehouse': [
                {'value': row.pk, 'label': str(row)}
                for row in scoped_queryset(WareHouse.objects.filter(is_active=True), warehouse_scope(request.user), 'pk').order_by('name', 'pk')
            ],
            'payment_account': payment_account_options(available_payment_accounts(request.warehouse_scope)),
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
                 'unit_label': f'{row.unit.name} ({row.unit.code})', 'cost_price': str(row.cost_price)}
                for row in Product.objects.select_related('unit').order_by('name', 'pk')
            ],
        })


class PurchaseEditOptionsAPIView(PurchaseOptionsAPIView):
    required_permissions = ('purchase.view_purchaseorder', 'purchase.change_purchaseorder', 'purchase.view_purchaseorderitem')
