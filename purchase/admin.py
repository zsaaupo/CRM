from django.contrib import admin

from warehouse.admin import WarehouseScopedAdmin

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseReturn, PurchaseReturnItem


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


@admin.register(PurchaseReturn)
class PurchaseReturnAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'purchase_order__warehouse_id'
    scoped_foreign_keys = {'purchase_order': 'warehouse_id'}
    posted_lookup = 'is_completed'
    list_display = ['return_number', 'purchase_order', 'return_date', 'is_completed']
    list_filter = ['is_completed', 'return_date']
    search_fields = ['return_number', 'purchase_order__order_number']
    list_select_related = ['purchase_order']

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return [*fields, 'purchase_order'] if obj else fields


@admin.register(PurchaseReturnItem)
class PurchaseReturnItemAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'purchase_return__purchase_order__warehouse_id'
    scoped_foreign_keys = {
        'purchase_return': 'purchase_order__warehouse_id',
        'purchase_order_item': 'purchase_order__warehouse_id',
    }
    posted_lookup = 'purchase_return__is_completed'
    list_display = ['purchase_return', 'purchase_order_item', 'quantity']
    search_fields = ['purchase_return__return_number', 'purchase_order_item__product__name', 'purchase_order_item__product__sku']
    list_select_related = ['purchase_return', 'purchase_order_item__purchase_order']
