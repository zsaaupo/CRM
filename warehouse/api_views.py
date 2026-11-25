from common.io_utils import ExportListMixin
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
from .adjustments import post_stock_adjustment
from .counts import create_stock_count, finalize_stock_count, import_count_final_file
from .models import (
    Stock, StockAdjustment, StockAdjustmentItem, StockBatch, StockCount, StockCountLine, StockSerial,
    RoleWarehouseAccess, WareHouse, WarehouseTransfer, WarehouseTransferItem,
)
from .opening import post_opening_stock
from .scope import HasWarehouseAccess, WarehouseScopeAPIMixin, all_warehouse_access, scoped_queryset, warehouse_scope
from .serializers import (
    StockAdjustmentSerializer, StockBatchReadSerializer, StockCountSerializer, StockFilterSerializer,
    StockReadSerializer, StockSerialReadSerializer, RoleWarehouseAccessFilterSerializer,
    RoleWarehouseAccessSerializer, WareHouseSerializer, WarehouseTransferSerializer, TransferCompleteSerializer,
)
from .services import (
    TransferBusy, complete_transfer, dispatch_transfer, import_transfer_csv, lock_transfer,
    receive_transfer, stock_valuation_chart, transfer_queryset,
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


class StockListAPIView(ExportListMixin, WarehouseScopeAPIMixin, BaseListAPIView):
    serializer_class = StockReadSerializer
    queryset = Stock.objects.select_related('warehouse', 'product__unit')
    search_fields = ['warehouse__name', 'warehouse__code', 'product__name', 'product__sku']
    ordering = ['warehouse__name', 'product__name', 'pk']
    export_filename = 'stock'
    export_title = 'Warehouse Stock'
    export_columns = [
        ('Warehouse', 'warehouse.name'), ('SKU', 'product.sku'),
        ('Product', 'product.name'), ('Quantity', 'quantity'), ('Average Cost', 'average_cost'),
    ]

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


class StockBatchListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    serializer_class = StockBatchReadSerializer
    queryset = StockBatch.objects.select_related('warehouse', 'product')
    search_fields = ['batch_number', 'product__sku', 'product__name', 'warehouse__name']
    ordering = ['expiry_date', 'batch_number', 'pk']

    def get_queryset(self):
        qs = super().get_queryset()
        wh = self.request.query_params.get('warehouse')
        prod = self.request.query_params.get('product')
        exp_before = self.request.query_params.get('expiry_before')
        exp_after = self.request.query_params.get('expiry_after')
        if wh and wh.isdigit():
            qs = qs.filter(warehouse_id=int(wh))
        if prod and prod.isdigit():
            qs = qs.filter(product_id=int(prod))
        if exp_before:
            qs = qs.filter(expiry_date__lte=exp_before)
        if exp_after:
            qs = qs.filter(expiry_date__gte=exp_after)
        return qs


class StockSerialListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    serializer_class = StockSerialReadSerializer
    queryset = StockSerial.objects.select_related('warehouse', 'product', 'batch')
    search_fields = ['serial_number', 'product__sku', 'product__name']
    ordering = ['product__sku', 'serial_number', 'pk']

    def get_queryset(self):
        qs = super().get_queryset()
        wh = self.request.query_params.get('warehouse')
        prod = self.request.query_params.get('product')
        st = self.request.query_params.get('status')
        if wh and wh.isdigit():
            qs = qs.filter(warehouse_id=int(wh))
        if prod and prod.isdigit():
            qs = qs.filter(product_id=int(prod))
        if st:
            qs = qs.filter(status=st)
        return qs


class OpeningStockPostAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.view_stock',)
    http_method_names = ['post', 'options']

    def post(self, request):
        from rest_framework import status as http_status
        rows = request.data.get('rows') or []
        req_key = request.data.get('request_key')
        if not req_key:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        from uuid import UUID
        try:
            key_uuid = UUID(str(req_key))
        except ValueError as exc:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'request_key': 'Invalid UUID.'}) from exc
        data, created = post_opening_stock(request.user, rows, key_uuid, request=request)
        return Response({**data, 'idempotent_replay': not created}, status=http_status.HTTP_200_OK)


class StockAdjustmentAPIMixin(WarehouseScopeAPIMixin):
    serializer_class = StockAdjustmentSerializer
    queryset = StockAdjustment.objects.select_related('warehouse').prefetch_related('items__product', 'items__unit')
    response_key = 'stock_adjustment'
    search_fields = ['reference_number', 'reason', 'note', 'warehouse__name']
    ordering = ['-adjustment_date', '-pk']


class StockAdjustmentListCreateAPIView(StockAdjustmentAPIMixin, BaseListCreateAPIView):
    pass


class StockAdjustmentDetailAPIView(StockAdjustmentAPIMixin, BaseDetailAPIView):
    def perform_destroy(self, instance):
        if instance.status == StockAdjustment.Status.POSTED:
            from common.exceptions import StateConflict
            raise StateConflict('Posted stock adjustments cannot be deleted.')
        super().perform_destroy(instance)


class StockAdjustmentUpdateAPIView(StockAdjustmentAPIMixin, BaseUpdateAPIView):
    pass


class StockAdjustmentPostAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ()
    http_method_names = ['post', 'options']

    def initial(self, request, *args, **kwargs):
        self.permission_classes = [IsAuthenticated]
        return super().initial(request, *args, **kwargs)

    def post(self, request, pk):
        from uuid import UUID
        from rest_framework.exceptions import ValidationError
        req_key = request.data.get('request_key')
        try:
            key_uuid = UUID(str(req_key)) if req_key else None
        except ValueError as exc:
            raise ValidationError({'request_key': 'Invalid UUID.'}) from exc
        if key_uuid is None:
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        adj, created = post_stock_adjustment(request.user, pk, key_uuid)
        data = StockAdjustmentSerializer(adj).data
        data['idempotent_replay'] = not created
        return Response(data)


class StockCountListCreateAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.manage_stock_count',)
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        qs = scoped_queryset(
            StockCount.objects.select_related('warehouse').prefetch_related('lines__product', 'lines__batch'),
            scope,
        )
        return Response({'results': StockCountSerializer(qs[:100], many=True).data})

    def post(self, request):
        from rest_framework import status as http_status
        wh_id = request.data.get('warehouse')
        if not wh_id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'warehouse': 'Choose a warehouse.'})
        sc = create_stock_count(
            request.user, int(wh_id),
            count_type=request.data.get('count_type') or StockCount.CountType.FULL,
            category_ids=request.data.get('categories') or None,
            brand_ids=request.data.get('brands') or None,
        )
        return Response(StockCountSerializer(sc).data, status=http_status.HTTP_201_CREATED)


class StockCountDetailAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.manage_stock_count',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        scope = warehouse_scope(request.user)
        qs = scoped_queryset(
            StockCount.objects.select_related('warehouse').prefetch_related('lines__product', 'lines__batch'),
            scope,
        )
        sc = get_object_or_404(qs, pk=pk)
        return Response(StockCountSerializer(sc).data)


class StockCountInitialFileAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.manage_stock_count',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        from django.http import HttpResponse
        scope = warehouse_scope(request.user)
        sc = get_object_or_404(scoped_queryset(StockCount.objects.all(), scope), pk=pk)
        if not sc.initial_file:
            from rest_framework.exceptions import NotFound
            raise NotFound('Initial count file not available.')
        sc.initial_file.open('rb')
        content = sc.initial_file.read()
        sc.initial_file.close()
        resp = HttpResponse(content, content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{sc.reference_number}_initial.csv"'
        return resp


class StockCountFinalFileAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.manage_stock_count',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        from rest_framework.exceptions import ValidationError
        upload = request.FILES.get('file')
        if upload is not None:
            sc = import_count_final_file(request.user, pk, upload, filename=upload.name)
        elif 'csv' in request.data:
            sc = import_count_final_file(request.user, pk, str(request.data['csv']), filename='final.csv')
        else:
            raise ValidationError({'file': 'Upload a final count CSV file or pass "csv".'})
        return Response(StockCountSerializer(sc).data)


class StockCountFinalizeAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.manage_stock_count',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        from uuid import UUID
        from rest_framework.exceptions import ValidationError
        req_key = request.data.get('request_key')
        try:
            key_uuid = UUID(str(req_key)) if req_key else None
        except ValueError as exc:
            raise ValidationError({'request_key': 'Invalid UUID.'}) from exc
        if key_uuid is None:
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        reconcile = bool(request.data.get('reconcile_intervening', False))
        sc, created = finalize_stock_count(request.user, pk, key_uuid, reconcile_intervening=reconcile)
        data = StockCountSerializer(sc).data
        data['idempotent_replay'] = not created
        return Response(data)


class WarehouseTransferDispatchAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    http_method_names = ['post', 'options']

    def initial(self, request, *args, **kwargs):
        from rest_framework.permissions import IsAuthenticated
        self.permission_classes = [IsAuthenticated]
        return super().initial(request, *args, **kwargs)

    def post(self, request, pk):
        record, dispatched_now = dispatch_transfer(request.user, pk, request_key=request.data.get('request_key'))
        data = WarehouseTransferSerializer(record, context={'request': request}).data
        data['dispatched_now'] = dispatched_now
        return Response(data)


class WarehouseTransferReceiveAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    http_method_names = ['post', 'options']

    def initial(self, request, *args, **kwargs):
        from rest_framework.permissions import IsAuthenticated
        self.permission_classes = [IsAuthenticated]
        return super().initial(request, *args, **kwargs)

    def post(self, request, pk):
        record, received_now = receive_transfer(
            request.user, pk,
            request_key=request.data.get('request_key'),
            lines=request.data.get('lines'),
            allow_partial=bool(request.data.get('allow_partial', False)),
        )
        data = WarehouseTransferSerializer(record, context={'request': request}).data
        data['received_now'] = received_now
        return Response(data)


class WarehouseTransferImportCSVAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    http_method_names = ['post', 'options']

    def initial(self, request, *args, **kwargs):
        from rest_framework.permissions import IsAuthenticated
        self.permission_classes = [IsAuthenticated]
        return super().initial(request, *args, **kwargs)

    def post(self, request):
        from decimal import Decimal
        from rest_framework import status as http_status
        from rest_framework.exceptions import ValidationError
        upload = request.FILES.get('file')
        csv_text = upload if upload is not None else request.data.get('csv')
        if csv_text is None:
            raise ValidationError({'file': 'Upload a CSV file or pass "csv".'})
        src = request.data.get('source_warehouse')
        dst = request.data.get('destination_warehouse')
        if not src or not dst:
            raise ValidationError({'warehouse': 'source_warehouse and destination_warehouse are required.'})
        record = import_transfer_csv(
            request.user,
            source_warehouse_id=int(src),
            destination_warehouse_id=int(dst),
            csv_content=csv_text,
            filename=upload.name if upload is not None else 'transfer.csv',
            status=request.data.get('status') or WarehouseTransfer.Status.PENDING,
            shipping_cost=Decimal(str(request.data.get('shipping_cost') or '0')),
            notes=request.data.get('notes') or '',
            attachment=request.FILES.get('attachment'),
        )
        return Response(
            WarehouseTransferSerializer(record, context={'request': request}).data,
            status=http_status.HTTP_201_CREATED,
        )


class StockValuationChartAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('warehouse.view_stock',)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        as_of = request.query_params.get('as_of') or None
        return Response(stock_valuation_chart(request.user, as_of=as_of))


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
