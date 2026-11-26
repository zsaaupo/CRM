from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import ProductStatus
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from inventory.models import Currency, ProductCategory, UnitOfMeasure
from people.models import Party
from warehouse.models import Stock, WareHouse
from warehouse.scope import WarehouseScopeAPIMixin, scoped_queryset, warehouse_scope_options

from .models import RawMaterial
from .serializers import RawMaterialDetailSerializer, RawMaterialSerializer
from .services import lock_raw_material


stock_quantity = Stock.objects.filter(
    product_id=OuterRef('product_id'), warehouse_id=OuterRef('warehouse_id'),
).values('quantity')[:1]


class RawMaterialListCreateAPIView(WarehouseScopeAPIMixin, BaseListCreateAPIView):
    queryset = RawMaterial.objects.select_related(
        'warehouse', 'product__unit', 'product__currency', 'product__status',
    ).annotate(on_hand=Coalesce(Subquery(stock_quantity), Value(Decimal('0'))))
    serializer_class = RawMaterialSerializer
    response_key = 'raw_material'
    search_fields = ['product__name', 'product__sku', 'product__barcode']
    ordering = RawMaterial._meta.ordering


class RawMaterialListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    queryset = RawMaterialListCreateAPIView.queryset
    serializer_class = RawMaterialSerializer
    search_fields = RawMaterialListCreateAPIView.search_fields
    ordering = RawMaterialListCreateAPIView.ordering


class RawMaterialDetailAPIView(WarehouseScopeAPIMixin, BaseDetailAPIView):
    queryset = RawMaterialListCreateAPIView.queryset.select_related('product__category', 'product__supplier')
    serializer_class = RawMaterialSerializer

    def get_serializer_class(self):
        if self.request.method in ('GET', 'HEAD'):
            return RawMaterialDetailSerializer
        return super().get_serializer_class()

    @transaction.atomic
    def perform_destroy(self, instance):
        instance = lock_raw_material(instance.pk, self.request.user)
        self.check_permissions(self.request)
        BaseDetailAPIView.perform_destroy(self, instance.product)


class RawMaterialUpdateAPIView(WarehouseScopeAPIMixin, BaseUpdateAPIView):
    queryset = RawMaterialListCreateAPIView.queryset
    serializer_class = RawMaterialSerializer


def choices(queryset, *, use_code=False, show_code=False):
    if use_code or show_code:
        rows = queryset.order_by('name', 'pk').values('id', 'name', 'code')
    else:
        rows = queryset.order_by('name', 'pk').values('id', 'name')
    return [{'value': row['code'] if use_code else row['id'],
             'label': f"{row['name']} ({row['code']})" if show_code and row['code'] else row['name']} for row in rows]


@method_decorator(never_cache, name='dispatch')
class RawMaterialOptionsAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('raw_material.add_rawmaterial',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk=None):
        scope = request.warehouse_scope
        warehouses = WareHouse.objects.filter(is_active=True)
        if pk is None:
            suppliers = Party.objects.filter(is_supplier=True)
        else:
            material = get_object_or_404(scoped_queryset(RawMaterial.objects.select_related('product'), scope), pk=pk)
            warehouses = WareHouse.objects.filter(Q(is_active=True) | Q(pk=material.warehouse_id))
            suppliers = Party.all_objects.filter(Q(is_deleted=False, is_supplier=True) | Q(pk=material.product.supplier_id))
        return Response({
            'warehouse': choices(scoped_queryset(warehouses, scope, 'pk'), show_code=True),
            'warehouse_scope': warehouse_scope_options(request.user),
            'status': choices(ProductStatus.objects.all(), use_code=True),
            'category': choices(ProductCategory.objects.all()),
            'unit': choices(UnitOfMeasure.objects.all(), show_code=True),
            'currency': choices(Currency.objects.all(), use_code=True, show_code=True),
            'supplier': choices(suppliers, show_code=True),
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
        })


class RawMaterialEditOptionsAPIView(RawMaterialOptionsAPIView):
    required_permissions = ('raw_material.view_rawmaterial', 'raw_material.change_rawmaterial')
