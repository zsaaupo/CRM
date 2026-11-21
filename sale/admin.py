from django.contrib import admin

from warehouse.admin import WarehouseScopedAdmin

from .models import SalesOrder, SalesOrderItem, SalesQuotation, SalesQuotationItem, SalesReturn, SalesReturnItem


@admin.register(SalesOrder)
class SalesOrderAdmin(WarehouseScopedAdmin):
    posted_lookup = 'is_completed'
    list_display = ['order_number', 'order_date', 'customer', 'warehouse', 'is_completed']
    list_filter = ['is_completed', 'order_date']
    search_fields = ['order_number', 'customer__name', 'customer_reference']
    list_select_related = ['customer', 'warehouse']


@admin.register(SalesOrderItem)
class SalesOrderItemAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'sales_order__warehouse_id'
    scoped_foreign_keys = {'sales_order': 'warehouse_id'}
    posted_lookup = 'sales_order__is_completed'
    list_display = ['sales_order', 'line_number', 'product', 'unit', 'quantity', 'unit_price']
    search_fields = ['sales_order__order_number', 'product__name', 'product__sku']
    list_select_related = ['sales_order', 'product', 'unit']


@admin.register(SalesReturn)
class SalesReturnAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'sales_order__warehouse_id'
    scoped_foreign_keys = {'sales_order': 'warehouse_id'}
    posted_lookup = 'is_completed'
    list_display = ['return_number', 'sales_order', 'return_date', 'is_completed']
    list_filter = ['is_completed', 'return_date']
    search_fields = ['return_number', 'sales_order__order_number']
    list_select_related = ['sales_order']


@admin.register(SalesReturnItem)
class SalesReturnItemAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'sales_return__sales_order__warehouse_id'
    scoped_foreign_keys = {'sales_return': 'sales_order__warehouse_id', 'sales_order_item': 'sales_order__warehouse_id'}
    posted_lookup = 'sales_return__is_completed'
    list_display = ['sales_return', 'sales_order_item', 'quantity']
    search_fields = ['sales_return__return_number', 'sales_order_item__product__name', 'sales_order_item__product__sku']
    list_select_related = ['sales_return', 'sales_order_item__sales_order']


@admin.register(SalesQuotation)
class SalesQuotationAdmin(WarehouseScopedAdmin):
    exclude = ['issued_document']
    posted_lookup = 'status'
    unposted_value = SalesQuotation.Status.DRAFT
    list_display = ['quotation_number', 'customer', 'warehouse', 'order_date', 'valid_until', 'status']
    list_filter = ['status', 'order_date']
    search_fields = ['quotation_number', 'customer__name', 'customer_reference']
    list_select_related = ['customer', 'warehouse']


@admin.register(SalesQuotationItem)
class SalesQuotationItemAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'quotation__warehouse_id'
    scoped_foreign_keys = {'quotation': 'warehouse_id'}
    posted_lookup = 'quotation__status'
    unposted_value = SalesQuotation.Status.DRAFT
    list_display = ['quotation', 'line_number', 'product', 'unit', 'quantity', 'unit_price']
    search_fields = ['quotation__quotation_number', 'product__name', 'product__sku']
    list_select_related = ['quotation', 'product', 'unit']
