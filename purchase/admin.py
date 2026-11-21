from django.contrib import admin

from warehouse.admin import WarehouseScopedAdmin

from .models import PurchaseOrder, PurchaseOrderItem


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(WarehouseScopedAdmin):
    posted_lookup = 'is_received'
    list_display = ['order_number', 'order_date', 'supplier', 'warehouse', 'is_received']
    list_filter = ['is_received', 'order_date']
    search_fields = ['order_number', 'supplier__name', 'supplier_reference']
    list_select_related = ['supplier', 'warehouse']


@admin.register(PurchaseOrderItem)
class PurchaseOrderItemAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'purchase_order__warehouse_id'
    scoped_foreign_keys = {'purchase_order': 'warehouse_id'}
    posted_lookup = 'purchase_order__is_received'
    list_display = ['purchase_order', 'line_number', 'product', 'unit', 'quantity', 'unit_price']
    search_fields = ['purchase_order__order_number', 'product__name', 'product__sku']
    list_select_related = ['purchase_order', 'product', 'unit']
