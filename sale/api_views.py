from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models.deletion import ProtectedError
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.services import available_payment_accounts, payment_account_options
from common.models import SettlementMode, TradeStatus
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView, RecordInUse
from inventory.models import Currency, Product
from people.models import Party
from warehouse.models import WareHouse
from warehouse.scope import (
    WarehouseScopeAPIMixin, WarehouseScopedSerializerMixin, lock_warehouse_scope, scoped_queryset,
    warehouse_scope, warehouse_scope_options,
)
from account.permissions import has_role_permissions

from .models import SalesOrder, SalesOrderItem, SalesQuotation, SalesQuotationItem, quotation_valid_until
from .services import (
    ACTION_PERMISSIONS, ITEM_VIEW, QUOTE_ADD, QUOTE_DETAIL, QuotationBusy, SaleBusy, Status,
    apply_quotation_action, available_actions, complete_sale, delete_order, lock_order,
    lock_quotation, require_draft, require_permissions,
)
from .serializers import (
    SalesOrderCreateSerializer, SalesOrderDetailSerializer, SalesOrderItemFilterSerializer,
    SalesOrderItemSerializer, SalesOrderLineSerializer, SalesOrderSerializer, SaleCompleteSerializer, replace_sales_lines,
)


class SalesConcurrencyMixin:
    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and ('locked' in str(exc).lower() or 'deadlock' in str(exc).lower()):
            exc = SaleBusy()
        return super().handle_exception(exc)


class SalesOrderListCreateAPIView(SalesConcurrencyMixin, WarehouseScopeAPIMixin, BaseListCreateAPIView):
    serializer_class = SalesOrderSerializer
    queryset = SalesOrder.objects.select_related(
        'currency', 'status', 'customer', 'warehouse', 'payment_account',
    ).prefetch_related('items')
    response_key = 'sales_order'
    search_fields = ['order_number', 'customer__name', 'customer__code', 'customer_reference', 'notes']
    ordering = ['-order_date', '-pk']

    def get_serializer_class(self):
        return SalesOrderCreateSerializer if self.request.method == 'POST' else SalesOrderSerializer

    def perform_create(self, serializer):
        if serializer.validated_data.get('items') and not has_role_permissions(self.request.user, ('sale.add_salesorderitem',)):
            raise PermissionDenied('You do not have permission to add sale order items.')
        super().perform_create(serializer)


class SalesOrderListAPIView(SalesConcurrencyMixin, WarehouseScopeAPIMixin, BaseListAPIView):
    serializer_class = SalesOrderSerializer
    queryset = SalesOrderListCreateAPIView.queryset
    search_fields = SalesOrderListCreateAPIView.search_fields
    ordering = SalesOrderListCreateAPIView.ordering


class SalesOrderDetailAPIView(SalesConcurrencyMixin, WarehouseScopeAPIMixin, BaseDetailAPIView):
    serializer_class = SalesOrderDetailSerializer
    queryset = SalesOrderListCreateAPIView.queryset

    def get_serializer_class(self):
        return SalesOrderCreateSerializer if self.request.method in ('PUT', 'PATCH') else SalesOrderDetailSerializer

    def perform_destroy(self, instance):
        try:
            delete_order(instance, self.request)
        except ProtectedError as exc:
            raise RecordInUse() from exc


class SalesOrderUpdateAPIView(SalesConcurrencyMixin, WarehouseScopeAPIMixin, BaseUpdateAPIView):
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


class SalesOrderItemListCreateAPIView(SalesConcurrencyMixin, SalesOrderItemQuerysetMixin, WarehouseScopeAPIMixin, BaseListCreateAPIView):
    warehouse_lookup = "sales_order__warehouse_id"
    serializer_class = SalesOrderItemSerializer
    queryset = SalesOrderItem.objects.select_related('sales_order', 'product', 'unit')
    response_key = 'sales_order_item'


class SalesOrderItemListAPIView(SalesConcurrencyMixin, SalesOrderItemQuerysetMixin, WarehouseScopeAPIMixin, BaseListAPIView):
    warehouse_lookup = "sales_order__warehouse_id"
    serializer_class = SalesOrderItemSerializer
    queryset = SalesOrderItemListCreateAPIView.queryset


class SalesOrderItemDetailAPIView(SalesConcurrencyMixin, WarehouseScopeAPIMixin, BaseDetailAPIView):
    warehouse_lookup = "sales_order__warehouse_id"
    serializer_class = SalesOrderItemSerializer
    queryset = SalesOrderItemListCreateAPIView.queryset

    @transaction.atomic
    def perform_destroy(self, instance):
        lock_order(instance.sales_order_id, self.request.user)
        super().perform_destroy(instance)


class SalesOrderItemUpdateAPIView(SalesConcurrencyMixin, WarehouseScopeAPIMixin, BaseUpdateAPIView):
    warehouse_lookup = "sales_order__warehouse_id"
    serializer_class = SalesOrderItemSerializer
    queryset = SalesOrderItemListCreateAPIView.queryset


@method_decorator(never_cache, name='dispatch')
class SaleCompleteAPIView(SalesConcurrencyMixin, WarehouseScopeAPIMixin, BaseAPIMixin, GenericAPIView):
    serializer_class = SaleCompleteSerializer
    required_permissions = ('sale.change_salesorder',)
    http_method_names = ['post', 'options']

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order, completed_now = complete_sale(request.user, serializer.validated_data['sale_id'])
        return Response({
            'message': 'Sale completed and stock updated.' if completed_now else 'Sale was already completed.',
            'completed_now': completed_now,
            'sales_order': SalesOrderDetailSerializer(order, context=self.get_serializer_context()).data,
        })


@method_decorator(never_cache, name='dispatch')
class SalesOptionsAPIView(SalesConcurrencyMixin, WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    """Reference data for sale creation without catalog management permissions."""

    required_permissions = ('sale.add_salesorder', 'sale.add_salesorderitem')
    http_method_names = ['get', 'head', 'options']
    customer_queryset = Party.objects.filter(is_customer=True)
    include_payment_accounts = True

    def get(self, request):
        options = {
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'warehouse_scope': warehouse_scope_options(request.user),
            'customer': [
                {'value': row.pk, 'label': f'{row.name} ({row.code})', 'address': row.address}
                for row in self.customer_queryset.order_by('name', 'pk')
            ],
            'warehouse': [
                {'value': row.pk, 'label': row.name}
                for row in scoped_queryset(WareHouse.objects.filter(is_active=True), warehouse_scope(request.user), 'pk').order_by('name', 'pk')
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
                 'sale_price': str(row.sale_price),
                 'minimum_sale_price': str(row.minimum_sale_price) if row.minimum_sale_price is not None else None,
                 'tax_rate': str(row.product_tax)}
                for row in Product.objects.select_related('unit').order_by('name', 'pk')
            ],
        }
        if self.include_payment_accounts:
            options['payment_account'] = payment_account_options(available_payment_accounts(request.warehouse_scope))
        return Response(options)


class SalesEditOptionsAPIView(SalesOptionsAPIView):
    required_permissions = ('sale.view_salesorder', 'sale.change_salesorder', 'sale.view_salesorderitem')

# Sales quotations

class QuotationSerializer(CatalogSerializer):
    customer_name = serializers.SerializerMethodField()
    warehouse_name = serializers.SerializerMethodField()
    status_name = serializers.SerializerMethodField()
    is_locked = serializers.SerializerMethodField()
    subtotal = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)
    grand_total = serializers.DecimalField(max_digits=40, decimal_places=4, read_only=True)

    class Meta:
        model = SalesQuotation
        fields = [
            'id', 'quotation_number', 'customer', 'customer_name', 'warehouse', 'warehouse_name', 'order_date',
            'valid_until', 'expected_delivery_date', 'currency', 'exchange_rate', 'customer_reference',
            'billing_address', 'shipping_address', 'shipping_cost', 'notes', 'terms', 'status', 'status_name', 'is_locked',
            'issued_at', 'issued_by', 'responded_at', 'responded_by', 'subtotal', 'tax_total', 'grand_total', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = AUDIT_FIELDS

    def get_customer_name(self, quotation):
        return quotation.issued_document['cards'][0]['name'] if quotation.issued_document else quotation.customer.name

    def get_warehouse_name(self, quotation):
        return quotation.issued_document['cards'][1]['name'] if quotation.issued_document else quotation.warehouse.name

    def get_status_name(self, quotation):
        return 'Expired' if quotation.is_expired else quotation.get_status_display()

    def get_is_locked(self, quotation):
        return quotation.status != Status.DRAFT


class QuotationItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name')
    product_sku = serializers.CharField(source='product.sku')
    unit_code = serializers.CharField(source='unit.code')
    unit_name = serializers.CharField(source='unit.name')
    line_total = serializers.DecimalField(max_digits=40, decimal_places=4)

    class Meta:
        model = SalesQuotationItem
        fields = ['id', 'line_number', 'product', 'product_name', 'product_sku', 'unit', 'unit_code', 'unit_name',
                  'quantity', 'unit_price', 'discount_amount', 'tax_rate', 'description', 'line_total']


class QuotationDetailSerializer(QuotationSerializer):
    items = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()
    sales_order_url = serializers.SerializerMethodField()
    revised_from_url = serializers.SerializerMethodField()

    class Meta(QuotationSerializer.Meta):
        fields = [*QuotationSerializer.Meta.fields, 'items', 'actions', 'sales_order_url', 'revised_from_url']

    def get_fields(self):
        fields = super().get_fields()
        if not has_role_permissions(self.context['request'].user, ITEM_VIEW):
            fields.pop('items')
        return fields

    def get_items(self, quotation):
        items = QuotationItemSerializer(quotation.items.all(), many=True).data
        if quotation.issued_document:
            saved_rows = {row['number']: row for row in quotation.issued_document['rows']}
            for item in items:
                saved = saved_rows[item['line_number']]
                item.update(product_name=saved['name'], product_sku=saved['sku'], unit_code=saved['unit'])
        return items

    def get_actions(self, quotation):
        return available_actions(quotation, self.context['request'].user)

    def get_sales_order_url(self, quotation):
        user = self.context['request'].user
        if quotation.sales_order_id and has_role_permissions(user, ('sale.view_salesorder',)):
            orders = scoped_queryset(SalesOrder.objects.filter(pk=quotation.sales_order_id), warehouse_scope(user))
            if orders.exists():
                return reverse('sale_pages:sale-detail', args=[quotation.sales_order_id])
        return None

    def get_revised_from_url(self, quotation):
        if quotation.revised_from_id:
            records = SalesQuotation.objects.filter(pk=quotation.revised_from_id)
            if scoped_queryset(records, warehouse_scope(self.context['request'].user)).exists():
                return reverse('sale_pages:quotation-detail', args=[quotation.revised_from_id])
        return None


class QuotationLineInput(SalesOrderLineSerializer):
    parent_field = 'quotation'

    class Meta(SalesOrderLineSerializer.Meta):
        model = SalesQuotationItem


class QuotationWriteSerializer(WarehouseScopedSerializerMixin, QuotationSerializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Party.objects.filter(is_customer=True, is_active=True))
    warehouse = serializers.PrimaryKeyRelatedField(queryset=WareHouse.objects.filter(is_active=True), required=False)
    currency = serializers.SlugRelatedField(slug_field='code', queryset=Currency.objects.all())
    items = QuotationLineInput(many=True, required=False, allow_empty=False, write_only=True)

    class Meta(QuotationSerializer.Meta):
        fields = [*QuotationSerializer.Meta.fields, 'items']

    def validate(self, attrs):
        if self.instance:
            require_draft(self.instance)
        elif 'valid_until' not in attrs:
            try:
                attrs['valid_until'] = quotation_valid_until(attrs.get('order_date'))
            except OverflowError as exc:
                raise serializers.ValidationError({'valid_until': 'Enter a validity date.'}) from exc
        items = attrs.pop('items', None)
        attrs = super().validate(attrs)
        if items is None:
            if self.instance is None:
                raise serializers.ValidationError({'items': 'Add at least one product.'})
            return attrs
        if self.instance is None and any('id' in item for item in items):
            raise serializers.ValidationError({'items': 'New quotation lines cannot include existing IDs.'})
        if len({item['line_number'] for item in items}) != len(items):
            raise serializers.ValidationError({'items': 'Line numbers must be unique within a quotation.'})
        return {**attrs, 'items': items}

    @transaction.atomic
    def create(self, validated_data):
        user = self.context['request'].user
        lock_warehouse_scope(user)
        require_permissions(user, QUOTE_ADD)
        values = self.validate(validated_data)
        items = values.pop('items')
        quotation = super().create(values)
        for item in items:
            quotation.items.create(create_by=quotation.create_by, modified_by=quotation.modified_by, **item)
        return quotation

    @transaction.atomic
    def update(self, instance, validated_data):
        user = self.context['request'].user
        scope = lock_warehouse_scope(user)
        require_permissions(user, ('sale.change_salesquotation',))
        self.instance = quotation = lock_quotation(instance.pk, scope)
        values = self.validate(validated_data)
        items = values.pop('items', None)
        if items is not None:
            replace_sales_lines(quotation, items, user.get_username(),
                                lambda action: require_permissions(user, (f'sale.{action}_salesquotationitem',)))
        return super().update(quotation, values)


class QuotationConcurrencyMixin:
    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and any(word in str(exc).lower() for word in ('locked', 'deadlock')):
            exc = QuotationBusy()
        return super().handle_exception(exc)


class QuotationResource(QuotationConcurrencyMixin, WarehouseScopeAPIMixin):
    queryset = SalesQuotation.objects.select_related('customer', 'warehouse').prefetch_related('items__product', 'items__unit')
    search_fields = ['quotation_number', 'customer__name', 'customer__code', 'customer_reference']
    ordering = ['-order_date', '-pk']


class QuotationListCreateAPIView(QuotationResource, BaseListCreateAPIView):
    response_key = 'sales_quotation'

    @property
    def required_permissions(self):
        return {**super().required_permissions, 'POST': QUOTE_ADD}

    def get_serializer_class(self):
        return QuotationWriteSerializer if self.request.method == 'POST' else QuotationSerializer


class QuotationDetailAPIView(QuotationResource, BaseDetailAPIView):
    def get_serializer_class(self):
        return QuotationWriteSerializer if self.request.method in ('PATCH', 'PUT') else QuotationDetailSerializer

    @transaction.atomic
    def perform_destroy(self, instance):
        user = self.request.user
        scope = lock_warehouse_scope(user)
        require_permissions(user, ('sale.delete_salesquotation',))
        current = lock_quotation(instance.pk, scope)
        require_draft(current)
        if current.items.exists():
            require_permissions(user, ('sale.delete_salesquotationitem',))
            current.items.all().delete()
        current.delete()


class QuotationOptionsAPIView(QuotationConcurrencyMixin, SalesOptionsAPIView):
    edit = False
    customer_queryset = Party.objects.filter(is_active=True, is_customer=True)
    include_payment_accounts = False

    @property
    def required_permissions(self):
        return (*QUOTE_DETAIL, 'sale.change_salesquotation') if self.edit else QUOTE_ADD


@method_decorator(never_cache, name='dispatch')
class QuotationActionAPIView(QuotationConcurrencyMixin, BaseAPIMixin, APIView):
    role_permissions_only = True
    http_method_names = ['post', 'options']

    @property
    def required_permissions(self):
        return ACTION_PERMISSIONS.get(self.kwargs['action'], ())

    def post(self, request, pk, action):
        payment_account = None
        if action == 'convert' and 'payment_account' in request.data:
            payment_account = serializers.IntegerField(min_value=1, max_value=2**63 - 1).run_validation(
                request.data['payment_account'],
            )
        mode = serializers.ChoiceField(choices=SettlementMode.choices).run_validation(
            request.data.get('settlement_mode', SettlementMode.AUTO_FULL))
        quotation = apply_quotation_action(request.user, pk, action, payment_account=payment_account, settlement_mode=mode)
        data = QuotationDetailSerializer(quotation, context={'request': request}).data
        return Response({'sales_quotation': data, 'quotation_url': reverse('sale_pages:quotation-detail', args=[quotation.pk])})
