from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied as APIPermissionDenied

from account.permissions import has_role_permissions
from common.admin import AuditedAdmin

from .access import check_assignment_scope, lock_assignment_write, managed_assignments
from .models import RoleWarehouseAccess, Stock, StockCount, StockCountLine, StockMovement, WareHouse, WarehouseTransfer, WarehouseTransferItem
from .scope import all_warehouse_access, has_warehouse_scope, lock_warehouse_scope, scoped_queryset, warehouse_scope
from .services import transfer_queryset


class WarehouseScopedAdmin(AuditedAdmin):
    warehouse_lookup = 'warehouse_id'
    scoped_foreign_keys = {'warehouse': 'pk'}
    all_warehouses_to_write = False
    posted_lookup = None
    unposted_value = False
    posted_filters = None

    def editable_filters(self):
        if self.posted_filters is not None:
            return self.posted_filters
        return {self.posted_lookup: self.unposted_value} if self.posted_lookup else {}

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        try:
            return self.scope_queryset(queryset, warehouse_scope(request.user))
        except APIPermissionDenied:
            return queryset.none()

    def scope_queryset(self, queryset, scope):
        return scoped_queryset(queryset, scope, self.warehouse_lookup)

    def has_warehouse_write_scope(self, request, *, require_all=False):
        try:
            scope = warehouse_scope(request.user)
        except APIPermissionDenied:
            return False
        return not require_all or all_warehouse_access(scope)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and self.has_warehouse_write_scope(
            request, require_all=self.all_warehouses_to_write or self.warehouse_lookup == 'pk')

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and has_warehouse_scope(request.user)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and self.has_warehouse_write_scope(
            request, require_all=self.all_warehouses_to_write) and self.is_unposted(obj)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and self.has_warehouse_write_scope(
            request, require_all=self.all_warehouses_to_write) and self.is_unposted(obj)

    def is_unposted(self, obj):
        if obj is None:
            return True
        for path, expected in self.editable_filters().items():
            value = obj
            for name in path.split('__'):
                value = getattr(value, name)
            if value != expected:
                return False
        return True

    def related_records(self, db_field, scope):
        queryset = db_field.related_model._default_manager.all()
        lookup = self.scoped_foreign_keys.get(db_field.name)
        if lookup is not None:
            queryset = scoped_queryset(queryset, scope, lookup)
        for path, expected in self.editable_filters().items():
            parent, _, state_field = path.partition('__')
            if state_field and parent == db_field.name:
                queryset = queryset.filter(**{state_field: expected})
        return queryset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        try:
            scope = warehouse_scope(request.user)
            if db_field.name in self.scoped_foreign_keys:
                kwargs['queryset'] = self.related_records(db_field, scope)
                if db_field.name == 'warehouse' and not all_warehouse_access(scope):
                    kwargs.update(required=True, initial=scope.warehouse_id)
        except APIPermissionDenied:
            kwargs['queryset'] = db_field.related_model._default_manager.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def lock_write_scope(self, request, *, creating=False):
        try:
            scope = lock_warehouse_scope(request.user)
        except APIPermissionDenied as exc:
            raise PermissionDenied(str(exc)) from exc
        if (self.all_warehouses_to_write or (creating and self.warehouse_lookup == 'pk')) and not all_warehouse_access(scope):
            raise PermissionDenied('This operation requires all-warehouse access.')
        return scope

    def check_existing_records(self, scope, ids):
        records = self.scope_queryset(self.model._default_manager.filter(pk__in=ids), scope)
        records = records.filter(**self.editable_filters())
        if set(records.select_for_update().values_list('pk', flat=True)) != set(ids):
            raise PermissionDenied('A selected record is completed or outside your warehouse access.')

    def save_model(self, request, obj, form, change):
        scope = self.lock_write_scope(request, creating=not change)
        if change:
            self.check_existing_records(scope, [obj.pk])
        for name in self.scoped_foreign_keys:
            field = self.opts.get_field(name)
            target_id = getattr(obj, field.attname)
            if target_id is None and all_warehouse_access(scope):
                continue
            related = self.related_records(field, scope).select_for_update()
            if not related.filter(pk=target_id).exists():
                raise PermissionDenied('A related record is completed or outside your warehouse access.')
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        scope = self.lock_write_scope(request)
        self.check_existing_records(scope, [obj.pk])
        super().delete_model(request, obj)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        scope = self.lock_write_scope(request)
        self.check_existing_records(scope, list(queryset.values_list('pk', flat=True)))
        super().delete_queryset(request, queryset)


@admin.register(WareHouse)
class WareHouseAdmin(WarehouseScopedAdmin):
    warehouse_lookup = 'pk'
    scoped_foreign_keys = {}
    list_display = ['name', 'code', 'phone', 'email', 'is_active']
    search_fields = ['name', 'code', 'phone', 'email']
    list_filter = ['is_active']
    ordering = ['name', 'pk']


class ReadOnlyWarehouseAdmin(WarehouseScopedAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockMovement)
class MovementAdmin(ReadOnlyWarehouseAdmin):
    list_display = ('business_date', 'warehouse', 'product', 'kind', 'quantity', 'source_id')
    search_fields = ('product__sku', 'posting_key')


@admin.register(StockCount)
class StockCountAdmin(ReadOnlyWarehouseAdmin):
    list_display = ('id', 'warehouse', 'created_at', 'create_by', 'finalized_at')


@admin.register(StockCountLine)
class CountLineAdmin(ReadOnlyWarehouseAdmin):
    warehouse_lookup = 'count__warehouse_id'
    list_display = ('count_id', 'product', 'expected', 'counted')


@admin.register(Stock)
class StockAdmin(ReadOnlyWarehouseAdmin):
    list_display = ['warehouse', 'product', 'quantity', 'updated_at']
    list_filter = ['warehouse']
    search_fields = ['warehouse__name', 'warehouse__code', 'product__name', 'product__sku']
    autocomplete_fields = ['warehouse', 'product']
    list_select_related = ['warehouse', 'product']
    ordering = ['warehouse__name', 'product__name', 'pk']


@admin.register(WarehouseTransfer)
class WarehouseTransferAdmin(WarehouseScopedAdmin):
    scoped_foreign_keys = {'source_warehouse': 'pk', 'destination_warehouse': 'pk'}
    all_warehouses_to_write = True
    posted_filters = {'is_completed': False, 'dispatched_at': None}
    list_display = ['transfer_number', 'transfer_date', 'source_warehouse', 'destination_warehouse', 'stage_label']
    list_filter = ['is_completed', 'transfer_date']
    search_fields = ['transfer_number', 'source_warehouse__name', 'destination_warehouse__name']
    list_select_related = ['source_warehouse', 'destination_warehouse']

    def scope_queryset(self, queryset, scope):
        return transfer_queryset(queryset, scope)


@admin.register(WarehouseTransferItem)
class WarehouseTransferItemAdmin(WarehouseScopedAdmin):
    scoped_foreign_keys = {'transfer': 'source_warehouse_id'}
    all_warehouses_to_write = True
    posted_filters = {'transfer__is_completed': False, 'transfer__dispatched_at': None}
    list_display = ['transfer', 'line_number', 'product', 'unit', 'quantity']
    search_fields = ['transfer__transfer_number', 'product__name', 'product__sku']
    list_select_related = ['transfer', 'product', 'unit']

    def scope_queryset(self, queryset, scope):
        transfers = transfer_queryset(WarehouseTransfer.objects.all(), scope)
        return queryset.filter(transfer__in=transfers)


@admin.register(RoleWarehouseAccess)
class RoleWarehouseAccessAdmin(AuditedAdmin):
    list_display = ['role', 'warehouse', 'all_warehouses']
    list_filter = ['all_warehouses']
    search_fields = ['role__name', 'warehouse__name', 'warehouse__code']
    list_select_related = ['role', 'warehouse']

    def can_manage_assignments(self, request):
        return has_role_permissions(
            request.user, ('warehouse.manage_warehouse_access',)) and has_warehouse_scope(request.user)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and self.can_manage_assignments(request)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and self.can_manage_assignments(request)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and self.can_manage_assignments(request)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and self.can_manage_assignments(request)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        try:
            return managed_assignments(queryset, request.user)
        except APIPermissionDenied:
            return queryset.none()

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return [*fields, 'role'] if obj else fields

    def check_assignment(self, request, obj):
        try:
            scope, _ = lock_assignment_write(request.user, obj.role_id, obj.pk)
            check_assignment_scope(scope, obj.warehouse_id, obj.all_warehouses)
        except (APIPermissionDenied, NotFound) as exc:
            raise PermissionDenied(str(exc)) from exc

    def save_model(self, request, obj, form, change):
        self.check_assignment(request, obj)
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        self.check_assignment(request, obj)
        super().delete_model(request, obj)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        for obj in queryset.order_by('pk'):
            self.check_assignment(request, obj)
        super().delete_queryset(request, queryset)
