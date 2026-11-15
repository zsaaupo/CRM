from django.contrib import admin

from .models import Stock, WareHouse


class WarehouseAuditAdmin(admin.ModelAdmin):
    readonly_fields = ['created_at', 'updated_at', 'create_by', 'modified_by']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.create_by = request.user.get_username()
        obj.modified_by = request.user.get_username()
        super().save_model(request, obj, form, change)


@admin.register(WareHouse)
class WareHouseAdmin(WarehouseAuditAdmin):
    list_display = ['name', 'code', 'phone', 'email', 'is_active']
    search_fields = ['name', 'code', 'phone', 'email']
    list_filter = ['is_active']
    ordering = ['name', 'pk']


@admin.register(Stock)
class StockAdmin(WarehouseAuditAdmin):
    list_display = ['warehouse', 'product', 'quantity', 'updated_at']
    list_filter = ['warehouse']
    search_fields = ['warehouse__name', 'warehouse__code', 'product__name', 'product__sku']
    autocomplete_fields = ['warehouse', 'product']
    list_select_related = ['warehouse', 'product']
    ordering = ['warehouse__name', 'product__name', 'pk']
