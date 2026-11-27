from django.db.models import Prefetch
from django.db.models.deletion import ProtectedError
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.response import Response
from rest_framework.views import APIView

from account.permissions import has_role_permissions
from accounting.models import base_currency
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView, RecordInUse
from people.models import Party
from warehouse.models import WareHouse
from warehouse.scope import WarehouseScopeAPIMixin, scoped_queryset, warehouse_scope_options

from .catalog_options import sales_currency_options, sales_product_options
from .models import Chalan, ChalanItem
from .permissions import CHALAN_ADD, CHALAN_ITEM_VIEW, CHALAN_VIEW
from .serializers import ChalanDetailSerializer, ChalanSerializer, ChalanWriteSerializer
from .services import delete_chalan


class ChalanQuerysetMixin(WarehouseScopeAPIMixin):
    queryset = Chalan.objects.select_related('customer', 'warehouse', 'delivery')
    serializer_class = ChalanSerializer
    search_fields = ['chalan_number', 'customer__name', 'customer__code', 'customer_reference']
    ordering = ['-chalan_date', '-pk']


class ChalanListCreateAPIView(ChalanQuerysetMixin, BaseListCreateAPIView):
    required_permissions = {'GET': CHALAN_VIEW, 'POST': CHALAN_ADD}
    queryset = ChalanQuerysetMixin.queryset.prefetch_related('items')
    response_key = 'chalan'

    def get_serializer_class(self):
        return ChalanWriteSerializer if self.request.method == 'POST' else super().get_serializer_class()


class ChalanListAPIView(ChalanQuerysetMixin, BaseListAPIView):
    queryset = ChalanListCreateAPIView.queryset


class ChalanDetailAPIView(ChalanQuerysetMixin, BaseDetailAPIView):
    def get_queryset(self):
        records = super().get_queryset()
        if self.request.method in ('GET', 'HEAD'):
            if has_role_permissions(self.request.user, CHALAN_ITEM_VIEW):
                return records.prefetch_related(Prefetch('items', queryset=ChalanItem.objects.select_related('product', 'unit')))
            return records.prefetch_related('items')
        return records

    def get_serializer_class(self):
        return ChalanWriteSerializer if self.request.method in ('PUT', 'PATCH') else ChalanDetailSerializer

    def perform_destroy(self, instance):
        try:
            delete_chalan(instance.pk, self.request.user)
        except ProtectedError as exc:
            raise RecordInUse() from exc


class ChalanUpdateAPIView(ChalanQuerysetMixin, BaseUpdateAPIView):
    serializer_class = ChalanWriteSerializer


@method_decorator(never_cache, name='dispatch')
class ChalanOptionsAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = CHALAN_ADD
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        warehouses = scoped_queryset(WareHouse.objects.filter(is_active=True), request.warehouse_scope, 'pk')
        return Response({
            'base_currency': base_currency(),
            'currency': sales_currency_options(),
            'warehouse_scope': warehouse_scope_options(request.user),
            'warehouse': [{'value': warehouse.pk, 'label': warehouse.name}
                          for warehouse in warehouses.order_by('name', 'pk')],
            'customer': [
                {'value': customer.pk, 'label': f'{customer.name} ({customer.code})', 'address': customer.address,
                 'recipient_name': customer.contact_person, 'recipient_phone': customer.phone}
                for customer in Party.objects.filter(is_customer=True).order_by('name', 'pk')
            ],
            'product': sales_product_options(include_unit_name=False),
        })
