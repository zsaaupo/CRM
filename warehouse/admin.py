from django.contrib import admin
from django.core.exceptions import PermissionDenied
from rest_framework.exceptions import PermissionDenied as APIPermissionDenied

from .models import Stock, WareHouse
from .scope import all_warehouse_access, check_warehouse, warehouse_scope, scoped_queryset
from account.permissions import has_role_permissions


class WarehouseAuditAdmin(admin.ModelAdmin):
    warehouse_lookup = 'pk'
    readonly_fields = ['created_at', 'updated_at', 'create_by', 'modified_by']

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        try:
            return scoped_queryset(queryset, warehouse_scope(request.user), self.warehouse_lookup)
        except APIPermissionDenied:
            return queryset.none()

    def has_add_permission(self, request):
        if not has_role_permissions(request.user, (f'{self.opts.app_label}.add_{self.opts.model_name}',)):
            return False
        try:
            scope = warehouse_scope(request.user)
            return self.warehouse_lookup != 'pk' or all_warehouse_access(scope)
        except APIPermissionDenied:
            return False

    def has_view_permission(self, request, obj=None):
        return has_role_permissions(request.user, (f'{self.opts.app_label}.view_{self.opts.model_name}',))

    def has_change_permission(self, request, obj=None):
        return has_role_permissions(request.user, (f'{self.opts.app_label}.change_{self.opts.model_name}',))

    def has_delete_permission(self, request, obj=None):
        return has_role_permissions(request.user, (f'{self.opts.app_label}.delete_{self.opts.model_name}',))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'warehouse':
            try:
                kwargs['queryset'] = scoped_queryset(WareHouse.objects.all(), warehouse_scope(request.user), 'pk')
            except APIPermissionDenied:
                kwargs['queryset'] = WareHouse.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        try:
            scope = warehouse_scope(request.user)
            if change or self.warehouse_lookup != 'pk':
                check_warehouse(scope, obj.pk if self.warehouse_lookup == 'pk' else obj.warehouse_id)
            elif not all_warehouse_access(scope):
                raise PermissionDenied('Creating warehouses requires all-warehouse access.')
        except APIPermissionDenied as exc:
            raise PermissionDenied(str(exc)) from exc
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
    warehouse_lookup = 'warehouse_id'
    list_display = ['warehouse', 'product', 'quantity', 'updated_at']
    list_filter = ['warehouse']
    search_fields = ['warehouse__name', 'warehouse__code', 'product__name', 'product__sku']
    autocomplete_fields = ['warehouse', 'product']
    list_select_related = ['warehouse', 'product']
    ordering = ['warehouse__name', 'product__name', 'pk']
