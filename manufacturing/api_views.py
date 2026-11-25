from decimal import Decimal
from uuid import UUID

from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.exceptions import StateConflict
from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView
from warehouse.scope import WarehouseScopeAPIMixin, check_warehouse, scoped_queryset, warehouse_scope
from .models import (
    BOMComponent, BillOfMaterials, ProductionConsumptionLine, ProductionOrder, RawMaterial, RawMaterialStock,
)
from .services import (
    approve_bom, post_production_order, preview_production_requirements,
    receive_raw_material, save_production_draft,
)


class RawMaterialSerializer(CatalogSerializer):
    class Meta:
        model = RawMaterial
        fields = [
            'id', 'code', 'name', 'category', 'stock_unit', 'current_unit_cost',
            'reorder_point', 'default_warehouse', 'specifications', 'is_active', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'current_unit_cost']


class RawMaterialStockSerializer(serializers.ModelSerializer):
    material_code = serializers.CharField(source='raw_material.code', read_only=True)
    material_name = serializers.CharField(source='raw_material.name', read_only=True)

    class Meta:
        model = RawMaterialStock
        fields = ['id', 'warehouse', 'raw_material', 'material_code', 'material_name', 'quantity', 'average_cost']
        read_only_fields = fields


class BOMComponentSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False, min_value=1)

    class Meta:
        model = BOMComponent
        fields = [
            'id', 'raw_material', 'unit', 'quantity_per_base_output',
            'wastage_percent', 'wastage_basis', 'issue_multiple',
        ]


class BillOfMaterialsSerializer(CatalogSerializer):
    components = BOMComponentSerializer(many=True, required=False)

    class Meta:
        model = BillOfMaterials
        fields = [
            'id', 'code', 'name', 'finished_product', 'version', 'base_output_qty',
            'status', 'approved_by', 'approved_at', 'notes', 'components', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'status', 'approved_by', 'approved_at']

    def create(self, validated_data):
        comps = validated_data.pop('components', [])
        bom = super().create(validated_data)
        for c in comps:
            c.pop('id', None)
            BOMComponent.objects.create(bom=bom, create_by=bom.create_by, modified_by=bom.modified_by, **c)
        return bom

    def update(self, instance, validated_data):
        if instance.status == BillOfMaterials.Status.APPROVED and instance.production_orders.exists():
            raise StateConflict('An approved BOM referenced by production orders is frozen. Create a new version.')
        comps = validated_data.pop('components', None)
        bom = super().update(instance, validated_data)
        if comps is not None:
            bom.components.all().delete()
            for c in comps:
                c.pop('id', None)
                BOMComponent.objects.create(bom=bom, create_by=self.context['request'].user.get_username(), modified_by=self.context['request'].user.get_username(), **c)
        return bom


class ProductionConsumptionLineSerializer(serializers.ModelSerializer):
    material_code = serializers.CharField(source='raw_material.code', read_only=True)

    class Meta:
        model = ProductionConsumptionLine
        fields = ['id', 'raw_material', 'material_code', 'planned_qty', 'available_qty_snapshot', 'actual_used_qty', 'unit_cost_snapshot', 'line_cost']
        read_only_fields = fields


class ProductionOrderSerializer(serializers.ModelSerializer):
    consumption_lines = ProductionConsumptionLineSerializer(many=True, read_only=True)

    class Meta:
        model = ProductionOrder
        fields = [
            'id', 'reference_number', 'bom', 'finished_product', 'material_warehouse',
            'finished_warehouse', 'planned_qty', 'accepted_good_qty', 'rejected_qty',
            'labor_cost', 'overhead_cost', 'material_cost_total', 'unit_cost_calculated',
            'status', 'production_date', 'posted_at', 'notes', 'consumption_lines', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = fields


class RawMaterialListCreateAPIView(BaseListCreateAPIView):
    serializer_class = RawMaterialSerializer
    queryset = RawMaterial.objects.select_related('category', 'stock_unit', 'default_warehouse')
    response_key = 'raw_material'
    search_fields = ['code', 'name']


class RawMaterialDetailAPIView(BaseDetailAPIView):
    serializer_class = RawMaterialSerializer
    queryset = RawMaterialListCreateAPIView.queryset


class RawMaterialStockListAPIView(WarehouseScopeAPIMixin, BaseListAPIView):
    serializer_class = RawMaterialStockSerializer
    queryset = RawMaterialStock.objects.select_related('warehouse', 'raw_material')
    search_fields = ['raw_material__code', 'raw_material__name']
    ordering = ['raw_material__code', 'pk']


class RawMaterialReceiveAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('manufacturing.manage_raw_material',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        req_key = request.data.get('request_key')
        if not req_key:
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        mv, created = receive_raw_material(
            request.user, pk, int(request.data.get('warehouse')),
            request.data.get('quantity') or 0,
            request.data.get('unit_cost') or 0,
            request.data.get('kind') or 'replenish',
            UUID(str(req_key)),
        )
        return Response({'movement_id': mv.pk, 'balance_after': str(mv.balance_after), 'idempotent_replay': not created})


class BOMListCreateAPIView(BaseListCreateAPIView):
    serializer_class = BillOfMaterialsSerializer
    queryset = BillOfMaterials.objects.select_related('finished_product').prefetch_related('components')
    response_key = 'bom'
    search_fields = ['code', 'name']


class BOMDetailAPIView(BaseDetailAPIView):
    serializer_class = BillOfMaterialsSerializer
    queryset = BOMListCreateAPIView.queryset


class BOMApproveAPIView(BaseAPIMixin, APIView):
    required_permissions = ('manufacturing.approve_bom',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        bom = approve_bom(request.user, pk)
        return Response(BillOfMaterialsSerializer(bom).data)


class ProductionPreviewAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request):
        mat_wh = int(request.data.get('material_warehouse'))
        check_warehouse(warehouse_scope(request.user), mat_wh)
        rows = preview_production_requirements(
            int(request.data.get('bom')),
            mat_wh,
            request.data.get('planned_qty') or 0,
        )
        return Response({'components': rows})


class ProductionOrderListCreateAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        scope = warehouse_scope(request.user)
        qs = scoped_queryset(
            ProductionOrder.objects.prefetch_related('consumption_lines__raw_material'),
            scope, lookup='finished_warehouse_id',
        )
        return Response({'results': ProductionOrderSerializer(qs[:100], many=True).data})

    def post(self, request):
        order = save_production_draft(
            request.user,
            bom_id=int(request.data.get('bom')),
            material_warehouse_id=int(request.data.get('material_warehouse')),
            finished_warehouse_id=int(request.data.get('finished_warehouse')),
            planned_qty=request.data.get('planned_qty') or 0,
            accepted_good_qty=request.data.get('accepted_good_qty'),
            rejected_qty=request.data.get('rejected_qty') or 0,
            labor_cost=request.data.get('labor_cost') or 0,
            overhead_cost=request.data.get('overhead_cost') or 0,
            notes=request.data.get('notes') or '',
        )
        return Response(ProductionOrderSerializer(order).data, status=status.HTTP_201_CREATED)


class ProductionOrderDetailAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        scope = warehouse_scope(request.user)
        order = get_object_or_404(
            scoped_queryset(ProductionOrder.objects.prefetch_related('consumption_lines__raw_material'), scope, lookup='finished_warehouse_id'),
            pk=pk,
        )
        return Response(ProductionOrderSerializer(order).data)


class ProductionOrderPostAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('manufacturing.post_production',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        req_key = request.data.get('request_key')
        if not req_key:
            raise ValidationError({'request_key': 'request_key UUID is required.'})
        overrides = request.data.get('consumption_overrides') or None
        order, created = post_production_order(
            request.user, pk, UUID(str(req_key)),
            consumption_overrides={int(k): v for k, v in overrides.items()} if overrides else None,
        )
        data = ProductionOrderSerializer(order).data
        data['idempotent_replay'] = not created
        return Response(data)


class ProductionOrderCancelAPIView(WarehouseScopeAPIMixin, BaseAPIMixin, APIView):
    required_permissions = ('manufacturing.post_production',)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        scope = warehouse_scope(request.user)
        order = get_object_or_404(
            scoped_queryset(ProductionOrder.objects.all(), scope, lookup='finished_warehouse_id'), pk=pk,
        )
        if order.status == ProductionOrder.Status.POSTED:
            raise StateConflict('Posted production orders cannot be cancelled.')
        order.status = ProductionOrder.Status.CANCELLED
        order.save(update_fields=['status', 'updated_at'])
        return Response(ProductionOrderSerializer(order).data)
