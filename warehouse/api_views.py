from common.view import (
    BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView,
    BaseUpdateAPIView,
)

from django.db import OperationalError, transaction
from django.db.models import Prefetch
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.generics import GenericAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from django.contrib.auth.models import Group
from django.shortcuts import get_object_or_404
from account.permissions import has_role_permissions

from .access import WarehouseAccessBusy, lock_assignment_write, managed_assignments
from .models import Stock, RoleWarehouseAccess, WareHouse, WarehouseTransfer, WarehouseTransferItem
from .scope import HasWarehouseAccess, WarehouseScopeAPIMixin, all_warehouse_access, scoped_queryset, warehouse_scope
from .serializers import (
    StockFilterSerializer, StockReadSerializer, RoleWarehouseAccessFilterSerializer,
    RoleWarehouseAccessSerializer, WareHouseSerializer,
    WarehouseTransferSerializer, TransferCompleteSerializer,
)
from .services import TransferBusy, complete_transfer, lock_transfer, transfer_queryset


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


class WarehouseTransferAPIMixin:
    role_permissions_only = True
    serializer_class = WarehouseTransferSerializer
    queryset = WarehouseTransfer.objects.select_related('source_warehouse', 'destination_warehouse').prefetch_related(
        Prefetch('items', queryset=WarehouseTransferItem.objects.select_related('product', 'unit')))
    response_key = 'warehouse_transfer'
    ordering = ['-transfer_date', '-pk']
    search_fields = ['transfer_number', 'source_warehouse__name', 'destination_warehouse__name',
                     'items__product__name', 'items__product__sku', 'notes']

    def get_permissions(self):
        return [*super().get_permissions(), HasWarehouseAccess()]

    def get_queryset(self):
        return transfer_queryset(super().get_queryset(), self.request.warehouse_scope,
            write=self.request.method in ('PUT', 'PATCH', 'DELETE')).distinct()

    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and ('locked' in str(exc).lower() or 'deadlock' in str(exc).lower()):
            exc = TransferBusy()
        return super().handle_exception(exc)


class WarehouseTransferListCreateAPIView(WarehouseTransferAPIMixin, BaseListCreateAPIView):
    pass


class WarehouseTransferListAPIView(WarehouseTransferAPIMixin, BaseListAPIView):
    pass


class WarehouseTransferDetailAPIView(WarehouseTransferAPIMixin, BaseDetailAPIView):
    @transaction.atomic
    def perform_destroy(self, instance):
        current = lock_transfer(instance.pk, self.request.user, 'delete')
        current.items.all().delete()
        super().perform_destroy(current)


class WarehouseTransferUpdateAPIView(WarehouseTransferAPIMixin, BaseUpdateAPIView):
    pass


@method_decorator(never_cache, name='dispatch')
class TransferCompleteAPIView(WarehouseTransferAPIMixin, BaseAPIMixin, GenericAPIView):
    serializer_class = TransferCompleteSerializer
    required_permissions = ('warehouse.change_warehousetransfer',)
    http_method_names = ['post', 'options']

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record, completed_now = complete_transfer(request.user, serializer.validated_data['transfer_id'])
        return Response({
            'message': 'Transfer completed and stock updated in both warehouses.' if completed_now
                       else 'Transfer was already completed. Stock was not moved again.',
            'completed_now': completed_now,
            'warehouse_transfer': WarehouseTransferSerializer(record, context=self.get_serializer_context()).data,
        })


@method_decorator(never_cache, name='dispatch')
class TransferOptionsAPIView(WarehouseTransferAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.add_warehousetransfer',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        if not all_warehouse_access(request.warehouse_scope):
            raise PermissionDenied('Creating or editing transfers requires access to both warehouses.')
        from inventory.models import Product
        warehouses = [{'value': row.pk, 'label': str(row)}
                      for row in WareHouse.objects.filter(is_active=True).order_by('name', 'pk')]
        return Response({
            'source_warehouse': warehouses, 'destination_warehouse': warehouses,
            'product': [{'value': row.pk, 'label': f'{row.sku} — {row.name}', 'unit': row.unit_id,
                         'unit_label': f'{row.unit.name} ({row.unit.code})'}
                        for row in Product.objects.select_related('unit').order_by('name', 'pk')],
        })


class TransferEditOptionsAPIView(TransferOptionsAPIView):
    required_permissions = ('warehouse.view_warehousetransfer', 'warehouse.change_warehousetransfer')


class RoleWarehouseAccessAPIMixin:
    role_permissions_only = True
    required_permissions = ('warehouse.manage_warehouse_access',)
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
        _, current = lock_assignment_write(self.request.user, instance.role_id, instance.pk)
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


@method_decorator(never_cache, name='dispatch')
class RoleWarehouseAccessOptionsAPIView(BaseAPIMixin, APIView):
    role_permissions_only = True
    required_permissions = ('warehouse.manage_warehouse_access',)
    http_method_names = ['get', 'head', 'options']

    class Filters(serializers.Serializer):
        role = serializers.IntegerField(min_value=1, max_value=9223372036854775807)

    def get(self, request):
        filters = self.Filters(data=request.query_params.dict())
        filters.is_valid(raise_exception=True)
        scope = warehouse_scope(request.user)
        role = get_object_or_404(Group, pk=filters.validated_data['role'])
        if not request.user.is_superuser and (
            request.user.groups.filter(pk=role.pk).exists() or role.user_set.filter(is_superuser=True).exists()
        ):
            raise PermissionDenied('Only a superuser can manage warehouse access for this role.')
        assignment = RoleWarehouseAccess.objects.select_related('role', 'warehouse').filter(role=role).first()
        if assignment:
            get_object_or_404(managed_assignments(RoleWarehouseAccess.objects.all(), request.user), pk=assignment.pk)
        return Response({
            'role': role.pk,
            'assignment': RoleWarehouseAccessSerializer(assignment).data if assignment else None,
            'warehouse': [{'value': row.pk, 'label': str(row)} for row in scoped_queryset(
                WareHouse.objects.all(), scope, 'pk').order_by('name', 'pk')],
            'can_assign_all': all_warehouse_access(scope),
            'can_create': True,
            'can_change': True,
        })
