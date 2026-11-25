from django.contrib import admin
from common.admin import AuditedAdmin
from warehouse.admin import WarehouseScopedAdmin
from .models import (
    BOMComponent, BillOfMaterials, ProductionConsumptionLine, ProductionOrder,
    RawMaterial, RawMaterialMovement, RawMaterialStock,
)


@admin.register(RawMaterial)
class RawMaterialAdmin(AuditedAdmin):
    list_display = ['code', 'name', 'stock_unit', 'current_unit_cost', 'reorder_point', 'is_active']
    search_fields = ['code', 'name']


@admin.register(RawMaterialStock)
class RawMaterialStockAdmin(WarehouseScopedAdmin):
    list_display = ['warehouse', 'raw_material', 'quantity', 'average_cost']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RawMaterialMovement)
class RawMaterialMovementAdmin(WarehouseScopedAdmin):
    list_display = ['warehouse', 'raw_material', 'kind', 'quantity_delta', 'unit_cost', 'balance_after', 'posting_key']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(AuditedAdmin):
    list_display = ['code', 'name', 'finished_product', 'version', 'base_output_qty', 'status']


@admin.register(BOMComponent)
class BOMComponentAdmin(AuditedAdmin):
    list_display = ['bom', 'raw_material', 'unit', 'quantity_per_base_output', 'wastage_percent', 'wastage_basis']


@admin.register(ProductionOrder)
class ProductionOrderAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'finished_warehouse_id'
    list_display = ['reference_number', 'finished_product', 'planned_qty', 'accepted_good_qty', 'unit_cost_calculated', 'status']


@admin.register(ProductionConsumptionLine)
class ProductionConsumptionLineAdmin(AuditedAdmin):
    list_display = ['production_order', 'raw_material', 'planned_qty', 'actual_used_qty', 'line_cost']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
