from django.contrib import admin

from common.admin import AuditedAdmin

from .models import Biller, BillerWarehouse, CustomerGroup, CustomerPointLedger, Lead, Party


@admin.register(CustomerGroup)
class CustomerGroupAdmin(AuditedAdmin):
    list_display = ['name', 'percentage', 'price_basis', 'is_active']
    search_fields = ['name']
    list_filter = ['price_basis', 'is_active']


@admin.register(Biller)
class BillerAdmin(AuditedAdmin):
    list_display = ['name', 'company_name', 'phone', 'email', 'is_active', 'is_deleted']
    search_fields = ['name', 'company_name', 'phone', 'email']
    list_filter = ['is_active', 'is_deleted']

    def get_queryset(self, request):
        return self.model.all_objects.all()

    def delete_model(self, request, obj):
        obj.delete(actor=request.user.get_username())

    def delete_queryset(self, request, queryset):
        queryset.delete(actor=request.user.get_username())


@admin.register(BillerWarehouse)
class BillerWarehouseAdmin(AuditedAdmin):
    list_display = ['biller', 'warehouse']
    list_select_related = ['biller', 'warehouse']


@admin.register(Lead)
class LeadAdmin(AuditedAdmin):
    list_display = ['name', 'company', 'status', 'assigned_to', 'follow_up_date', 'expected_close_date']
    search_fields = ['name', 'company', 'email', 'phone']
    list_filter = ['status']
    list_select_related = ['assigned_to']


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'is_customer', 'is_supplier', 'phone', 'email', 'is_active',
                    'is_deleted', 'deleted_at', 'deleted_by']
    search_fields = ['code', 'name', 'contact_person', 'phone', 'email']
    list_filter = ['is_deleted', 'is_customer', 'is_supplier', 'is_active']
    readonly_fields = ['created_at', 'updated_at', 'create_by', 'modified_by',
                       'deposit_balance', 'reward_points',
                       'is_deleted', 'deleted_at', 'deleted_by']

    def get_queryset(self, request):
        queryset = self.model.all_objects.all()
        ordering = self.get_ordering(request)
        return queryset.order_by(*ordering) if ordering else queryset

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_deleted:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_deleted:
            return False
        return super().has_delete_permission(request, obj)

    def get_deleted_objects(self, objs, request):
        # Soft deletion preserves purchases and other references; no related rows are deleted.
        parties = [obj for obj in objs if not obj.is_deleted]
        permissions = set()
        if any(not self.has_delete_permission(request, obj) for obj in parties):
            permissions.add(str(self.opts.verbose_name))
        return ([str(obj) for obj in parties], {str(self.opts.verbose_name_plural): len(parties)}, permissions, [])

    def save_model(self, request, obj, form, change):
        if not change:
            obj.create_by = request.user.get_username()
        obj.modified_by = request.user.get_username()
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        obj.delete(actor=request.user.get_username())

    def delete_queryset(self, request, queryset):
        queryset.delete(actor=request.user.get_username())


@admin.register(CustomerPointLedger)
class CustomerPointLedgerAdmin(admin.ModelAdmin):
    list_display = ['customer', 'reason', 'points_delta', 'sales_order', 'expires_at', 'request_key']
    list_filter = ['reason']
    list_select_related = ['customer', 'sales_order']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
