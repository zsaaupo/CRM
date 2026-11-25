"""M3 API views: Quotation->Purchase conversion, POS registers/checkout/drafts, Discount Plans/Coupons/Gift Cards, Delivery/Packing Slip/Challan."""

from decimal import Decimal
from uuid import UUID, uuid4

from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from account.permissions import has_role_permissions
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListCreateAPIView
from inventory.models import Product
from warehouse.models import Stock
from warehouse.scope import WarehouseScopeAPIMixin, check_warehouse, scoped_queryset, warehouse_scope
from .loyalty import issue_gift_card, recharge_gift_card, validate_coupon
from .models import (
    CashRegisterSession, Challan, Coupon, Courier, Delivery, Discount, DiscountPlan,
    GiftCard, PackingSlip, PosDraft, RegisterMovement, SalesOrder,
)
from .pos import close_register, open_register, pos_checkout, record_register_movement, register_summary
from .services import convert_quotation_to_purchase
from .shipping import (
    close_challan, create_challan, create_delivery, create_packing_slip,
    dispatch_challan, update_delivery_status, whatsapp_link,
)


# ---------------------------------------------------------------------------
# QUO-01
# ---------------------------------------------------------------------------

class ConvertQuotationPurchaseAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('sale.view_salesquotation', 'purchase.add_purchaseorder')
    http_method_names = ['get', 'post', 'options']

    def get(self, request, pk):
        supplier_id = request.query_params.get('supplier')
        cost_basis = request.query_params.get('cost_basis') or 'product_cost'
        if not supplier_id:
            raise ValidationError({'supplier': 'supplier query parameter is required.'})
        data = convert_quotation_to_purchase(
            request.user, pk, int(supplier_id), cost_basis, uuid4(), preview=True,
        )
        return Response(data)

    def post(self, request, pk):
        supplier_id = request.data.get('supplier')
        cost_basis = request.data.get('cost_basis')
        req_key = request.data.get('request_key')
        if not supplier_id or not cost_basis or not req_key:
            raise ValidationError({'detail': 'supplier, cost_basis, and request_key are required.'})
        try:
            key_uuid = UUID(str(req_key))
        except ValueError as exc:
            raise ValidationError({'request_key': 'Invalid UUID.'}) from exc
        data, created = convert_quotation_to_purchase(
            request.user, pk, int(supplier_id), cost_basis, key_uuid, preview=False,
        )
        return Response({**data, 'idempotent_replay': not created}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POS-01
# ---------------------------------------------------------------------------

class CashRegisterSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashRegisterSession
        fields = [
            'id', 'user', 'warehouse', 'opened_at', 'closed_at', 'opening_cash',
            'closing_cash_expected', 'closing_cash_actual', 'variance', 'status', 'notes',
        ]
        read_only_fields = fields


class RegisterOpenAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request):
        wh_id = request.data.get('warehouse')
        req_key = request.data.get('request_key')
        if not wh_id or not req_key:
            raise ValidationError({'detail': 'warehouse and request_key are required.'})
        session, created = open_register(
            request.user, int(wh_id), request.data.get('opening_cash') or 0, UUID(str(req_key)),
        )
        data = CashRegisterSessionSerializer(session).data
        data['idempotent_replay'] = not created
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class RegisterCloseAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        req_key = request.data.get('request_key')
        if not req_key:
            raise ValidationError({'request_key': 'request_key is required.'})
        session, created = close_register(
            request.user, pk, request.data.get('counted_cash', 0),
            notes=request.data.get('notes') or '', request_key=UUID(str(req_key)),
        )
        data = CashRegisterSessionSerializer(session).data
        data['summary'] = register_summary(session.pk)
        data['idempotent_replay'] = not created
        return Response(data)


class RegisterSummaryAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        scope = warehouse_scope(request.user)
        session = get_object_or_404(scoped_queryset(CashRegisterSession.objects.all(), scope), pk=pk)
        return Response(register_summary(session.pk))


class RegisterMovementCreateAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        mv, created = record_register_movement(
            request.user, pk, request.data.get('kind'), request.data.get('amount') or 0,
            account_id=request.data.get('account'), note=request.data.get('note') or '',
            request_key=UUID(str(request.data['request_key'])) if request.data.get('request_key') else None,
        )
        return Response({'id': mv.pk, 'kind': mv.kind, 'amount': str(mv.amount), 'idempotent_replay': not created})


class PosCheckoutAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request):
        req_key = request.data.get('request_key')
        if not req_key:
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        res, created = pos_checkout(request.user, dict(request.data), UUID(str(req_key)))
        return Response({**res, 'idempotent_replay': not created}, status=status.HTTP_200_OK)


class PosDraftSerializer(serializers.ModelSerializer):
    class Meta:
        model = PosDraft
        fields = ['id', 'user', 'warehouse', 'customer', 'cart', 'updated_at']
        read_only_fields = ['id', 'user', 'updated_at']


class PosDraftListCreateAPIView(WarehouseScopeAPIMixin, BaseListCreateAPIView):
    serializer_class = PosDraftSerializer
    queryset = PosDraft.objects.all()
    permission_classes = [IsAuthenticated]
    response_key = 'pos_draft'
    ordering = ['-updated_at', '-pk']
    search_fields = ['warehouse__name']

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        scope = warehouse_scope(self.request.user)
        wh = serializer.validated_data['warehouse']
        check_warehouse(scope, wh.pk)
        serializer.save(user=self.request.user, create_by=self.request.user.get_username(), modified_by=self.request.user.get_username())


class PosDraftDetailAPIView(WarehouseScopeAPIMixin, BaseDetailAPIView):
    serializer_class = PosDraftSerializer
    queryset = PosDraft.objects.all()
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


class PosProductsGridAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        wh_id = request.query_params.get('warehouse')
        if not wh_id or not str(wh_id).isdigit():
            raise ValidationError({'warehouse': 'warehouse query param is required.'})
        wh_pk = int(wh_id)
        check_warehouse(scope, wh_pk)

        qs = Product.objects.select_related('unit', 'category', 'brand').order_by('name', 'pk')
        if request.query_params.get('category'):
            qs = qs.filter(category_id=int(request.query_params['category']))
        if request.query_params.get('brand'):
            qs = qs.filter(brand_id=int(request.query_params['brand']))
        if request.query_params.get('featured') in ('1', 'true', 'True'):
            qs = qs.filter(is_featured=True)
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__iexact=q))

        products = list(qs[:60])
        stocks = {
            s.product_id: s.quantity - s.oversold_quantity
            for s in Stock.objects.filter(warehouse_id=wh_pk, product_id__in=[p.pk for p in products])
        }
        return Response({
            'results': [
                {
                    'id': p.pk,
                    'sku': p.sku,
                    'barcode': p.barcode,
                    'name': p.name,
                    'sale_price': str(p.sale_price),
                    'unit': p.sale_unit_id or p.unit_id,
                    'stock_qty': str(stocks.get(p.pk, Decimal('0'))),
                    'has_batch_expiry': p.has_batch_expiry,
                    'has_imei_serial': p.has_imei_serial,
                }
                for p in products
            ],
        })


class RecentSalesOrdersAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('sale.view_salesorder',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        limit = min(int(request.query_params.get('limit') or 10), 50)
        scope = warehouse_scope(request.user)
        orders = scoped_queryset(
            SalesOrder.objects.select_related('customer', 'warehouse').order_by('-pk'),
            scope,
        )[:limit]
        return Response({
            'results': [
                {
                    'id': o.pk,
                    'order_number': o.order_number,
                    'order_date': str(o.order_date),
                    'customer_name': o.customer.name if o.customer else 'Walk-in',
                    'channel': o.channel,
                    'grand_total': str(o.grand_total),
                    'payment_status': o.payment_status,
                }
                for o in orders
            ],
        })


# ---------------------------------------------------------------------------
# PRICE-01
# ---------------------------------------------------------------------------

class DiscountPlanSerializer(CatalogSerializer):
    class Meta:
        model = DiscountPlan
        fields = ['id', 'name', 'is_active', 'customers', 'customer_groups', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class DiscountSerializer(CatalogSerializer):
    class Meta:
        model = Discount
        fields = [
            'id', 'plan', 'name', 'valid_from', 'valid_till', 'discount_type', 'value',
            'minimum_qty', 'maximum_qty', 'days_of_week', 'applies_to', 'products', 'categories',
            'is_active', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = AUDIT_FIELDS


class CouponSerializer(CatalogSerializer):
    class Meta:
        model = Coupon
        fields = ['id', 'code', 'coupon_type', 'amount', 'minimum_amount', 'quantity', 'used_count', 'expired_date', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'used_count']


class GiftCardSerializer(CatalogSerializer):
    class Meta:
        model = GiftCard
        fields = ['id', 'card_no', 'amount', 'balance', 'customer', 'expired_date', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'balance']


class DiscountPlanListCreateAPIView(BaseListCreateAPIView):
    serializer_class = DiscountPlanSerializer
    queryset = DiscountPlan.objects.prefetch_related('customers', 'customer_groups')
    response_key = 'discount_plan'


class DiscountPlanDetailAPIView(BaseDetailAPIView):
    serializer_class = DiscountPlanSerializer
    queryset = DiscountPlanListCreateAPIView.queryset


class DiscountListCreateAPIView(BaseListCreateAPIView):
    serializer_class = DiscountSerializer
    queryset = Discount.objects.select_related('plan').prefetch_related('products', 'categories')
    response_key = 'discount'


class DiscountDetailAPIView(BaseDetailAPIView):
    serializer_class = DiscountSerializer
    queryset = DiscountListCreateAPIView.queryset


class CouponListCreateAPIView(BaseListCreateAPIView):
    serializer_class = CouponSerializer
    queryset = Coupon.objects.all()
    response_key = 'coupon'
    search_fields = ['code']
    ordering = ['code', 'pk']


class CouponDetailAPIView(BaseDetailAPIView):
    serializer_class = CouponSerializer
    queryset = Coupon.objects.all()


class CouponValidateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request):
        code = request.data.get('code') or ''
        subtotal = Decimal(str(request.data.get('subtotal') or '0'))
        coupon, disc = validate_coupon(code, subtotal)
        return Response({'valid': True, 'coupon_id': coupon.pk, 'code': coupon.code, 'discount_amount': str(disc)})


class GiftCardListCreateAPIView(BaseListCreateAPIView):
    serializer_class = GiftCardSerializer
    queryset = GiftCard.objects.select_related('customer')
    response_key = 'gift_card'
    search_fields = ['card_no']
    ordering = ['-created_at', '-pk']

    def create(self, request, *args, **kwargs):
        card, created = issue_gift_card(
            request.user,
            request.data.get('card_no') or '',
            request.data.get('amount') or 0,
            customer=request.data.get('customer'),
            expired_date=request.data.get('expired_date'),
            account_id=request.data.get('account'),
            request_key=UUID(str(request.data['request_key'])) if request.data.get('request_key') else None,
        )
        return Response(
            {'gift_card': GiftCardSerializer(card).data, 'idempotent_replay': not created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class GiftCardDetailAPIView(BaseDetailAPIView):
    serializer_class = GiftCardSerializer
    queryset = GiftCard.objects.select_related('customer')


class GiftCardRechargeAPIView(BaseAPIMixin, APIView):
    required_permissions = ('sale.manage_gift_card',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        card, created = recharge_gift_card(
            request.user, pk, request.data.get('amount') or 0,
            account_id=request.data.get('account'),
            request_key=UUID(str(request.data['request_key'])) if request.data.get('request_key') else None,
        )
        return Response({'gift_card': GiftCardSerializer(card).data, 'idempotent_replay': not created})


# ---------------------------------------------------------------------------
# SHIP-01
# ---------------------------------------------------------------------------

class CourierSerializer(CatalogSerializer):
    class Meta:
        model = Courier
        fields = ['id', 'name', 'phone', 'address', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class DeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = Delivery
        fields = [
            'id', 'reference_number', 'sales_order', 'courier', 'delivered_by', 'received_by',
            'customer', 'address', 'status', 'attachment', 'note', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = fields


class PackingSlipSerializer(serializers.ModelSerializer):
    class Meta:
        model = PackingSlip
        fields = ['id', 'reference_number', 'sales_order', 'delivery', 'status', *AUDIT_FIELDS[1:]]
        read_only_fields = fields


class ChallanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Challan
        fields = [
            'id', 'reference_number', 'courier', 'status', 'closed_by',
            'closed_at', 'closing_date', 'amount', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = fields


class CourierListCreateAPIView(BaseListCreateAPIView):
    serializer_class = CourierSerializer
    queryset = Courier.objects.all()
    response_key = 'courier'


class CourierDetailAPIView(BaseDetailAPIView):
    serializer_class = CourierSerializer
    queryset = Courier.objects.all()


class DeliveryListCreateAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('sale.add_delivery',)
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        qs = scoped_queryset(Delivery.objects.all(), scope, lookup='sales_order__warehouse_id')
        return Response({'results': DeliverySerializer(qs[:100], many=True).data})

    def post(self, request):
        deliv = create_delivery(
            request.user, int(request.data.get('sales_order')),
            request.data.get('items') or [],
            courier_id=request.data.get('courier'),
            address=request.data.get('address') or '',
            note=request.data.get('note') or '',
            delivered_by=request.data.get('delivered_by') or '',
        )
        return Response(DeliverySerializer(deliv).data, status=status.HTTP_201_CREATED)


class DeliveryDetailAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('sale.view_delivery',)
    http_method_names = ['get', 'patch', 'head', 'options']

    def get(self, request, pk):
        scope = warehouse_scope(request.user)
        deliv = get_object_or_404(scoped_queryset(Delivery.objects.all(), scope, lookup='sales_order__warehouse_id'), pk=pk)
        return Response(DeliverySerializer(deliv).data)

    def patch(self, request, pk):
        deliv = update_delivery_status(
            request.user, pk, request.data.get('status'),
            received_by=request.data.get('received_by') or '',
        )
        return Response(DeliverySerializer(deliv).data)


class PackingSlipListCreateAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('sale.manage_packing_slip',)
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        qs = scoped_queryset(PackingSlip.objects.all(), scope, lookup='sales_order__warehouse_id')
        return Response({'results': PackingSlipSerializer(qs[:100], many=True).data})

    def post(self, request):
        slip = create_packing_slip(
            request.user, int(request.data.get('sales_order')),
            request.data.get('items') or [],
            delivery_id=request.data.get('delivery'),
        )
        return Response(PackingSlipSerializer(slip).data, status=status.HTTP_201_CREATED)


class PackingSlipDetailAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('sale.manage_packing_slip',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        scope = warehouse_scope(request.user)
        slip = get_object_or_404(scoped_queryset(PackingSlip.objects.all(), scope, lookup='sales_order__warehouse_id'), pk=pk)
        return Response(PackingSlipSerializer(slip).data)


class ChallanListCreateAPIView(BaseAPIMixin, APIView):
    required_permissions = ('sale.issue_challan',)
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        return Response({'results': ChallanSerializer(Challan.objects.all()[:100], many=True).data})

    def post(self, request):
        challan = create_challan(
            request.user,
            request.data.get('packing_slips') or [],
            int(request.data.get('courier')),
            amount=Decimal(str(request.data.get('amount') or '0')),
        )
        return Response(ChallanSerializer(challan).data, status=status.HTTP_201_CREATED)


class ChallanDetailAPIView(BaseAPIMixin, APIView):
    required_permissions = ('sale.view_challan',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        return Response(ChallanSerializer(get_object_or_404(Challan, pk=pk)).data)


class ChallanDispatchAPIView(BaseAPIMixin, APIView):
    required_permissions = ('sale.issue_challan',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        return Response(ChallanSerializer(dispatch_challan(request.user, pk)).data)


class ChallanCloseAPIView(BaseAPIMixin, APIView):
    required_permissions = ('sale.close_challan',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        req_key = request.data.get('request_key')
        if not req_key:
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        closing_date = request.data.get('closing_date')
        if isinstance(closing_date, str) and closing_date:
            closing_date = timezone.datetime.fromisoformat(closing_date).date()
        challan, created = close_challan(request.user, pk, UUID(str(req_key)), closing_date=closing_date)
        data = ChallanSerializer(challan).data
        data['idempotent_replay'] = not created
        return Response(data)


class SalesWhatsAppLinkAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('sale.view_salesorder',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        scope = warehouse_scope(request.user)
        order = get_object_or_404(scoped_queryset(SalesOrder.objects.select_related('customer'), scope), pk=pk)
        return Response({'whatsapp_url': whatsapp_link(order)})
