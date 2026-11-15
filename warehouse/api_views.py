from common.view import (
    BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView,
    BaseUpdateAPIView,
)

from django.db import OperationalError, transaction
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.generics import RetrieveAPIView

from .access import WarehouseAccessBusy, lock_assignment_write, managed_assignments
from .models import Stock, RoleWarehouseAccess, WareHouse
from .scope import WarehouseScopeAPIMixin
from .serializers import (
    StockFilterSerializer, StockReadSerializer, RoleWarehouseAccessFilterSerializer,
    RoleWarehouseAccessSerializer, WareHouseSerializer,
)


class WarehouseRecordScopeMixin(WarehouseScopeAPIMixin):
    warehouse_lookup = 'pk'


class WareHouseListCreateAPIView(WarehouseRecordScopeMixin, BaseListCreateAPIView):
    serializer_class = WareHouseSerializer
    queryset = WareHouse.objects.all()
    response_key = 'warehouse'
    search_fields = ['name', 'code', 'address', 'phone', 'email']


class WareHouseListAPIView(WarehouseRecordScopeMixin, BaseListAPIView):
    serializer_class = WareHouseSerializer
    queryset = WareHouse.objects.all()
    search_fields = WareHouseListCreateAPIView.search_fields


class WareHouseDetailAPIView(WarehouseRecordScopeMixin, BaseDetailAPIView):
    serializer_class = WareHouseSerializer
    queryset = WareHouse.objects.all()


class WareHouseUpdateAPIView(WarehouseRecordScopeMixin, BaseUpdateAPIView):
    serializer_class = WareHouseSerializer
    queryset = WareHouse.objects.all()


class StockListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    serializer_class = StockReadSerializer
    queryset = Stock.objects.select_related('warehouse', 'product__unit')
    search_fields = ['warehouse__name', 'warehouse__code', 'product__name', 'product__sku']
    ordering = ['warehouse__name', 'product__name', 'pk']

    def get_queryset(self):
        queryset = super().get_queryset()
        filters = StockFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return queryset.filter(**{f'{name}_id': value for name, value in filters.validated_data.items()})


@method_decorator(never_cache, name='dispatch')
class StockDetailAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, RetrieveAPIView):
    serializer_class = StockReadSerializer
    queryset = StockListAPIView.queryset
    http_method_names = ['get', 'head', 'options']


class RoleWarehouseAccessAPIMixin:
    role_permissions_only = True
    serializer_class = RoleWarehouseAccessSerializer
    queryset = RoleWarehouseAccess.objects.select_related('role', 'warehouse')
    response_key = 'role_warehouse_access'
    search_fields = ['role__name', 'warehouse__name', 'warehouse__code']
    ordering = ['role_id', 'pk']

    def get_queryset(self):
        queryset = managed_assignments(super().get_queryset(), self.request.user)
        # Preserve omission: BooleanField treats missing HTML/query fields as False.
        filters = RoleWarehouseAccessFilterSerializer(data=self.request.query_params.dict())
        filters.is_valid(raise_exception=True)
        return queryset.filter(**filters.validated_data)

    @transaction.atomic
    def perform_destroy(self, instance):
        _, current = lock_assignment_write(self.request.user, instance.role_id, instance.pk, action='delete')
        super().perform_destroy(current)

    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and ('locked' in str(exc).lower() or 'deadlock' in str(exc).lower()):
            exc = WarehouseAccessBusy()
        return super().handle_exception(exc)


class RoleWarehouseAccessListCreateAPIView(RoleWarehouseAccessAPIMixin, BaseListCreateAPIView):
    pass


class RoleWarehouseAccessListAPIView(RoleWarehouseAccessAPIMixin, BaseListAPIView):
    pass


class RoleWarehouseAccessDetailAPIView(RoleWarehouseAccessAPIMixin, BaseDetailAPIView):
    pass


class RoleWarehouseAccessUpdateAPIView(RoleWarehouseAccessAPIMixin, BaseUpdateAPIView):
    pass
