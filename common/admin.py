from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import ERPBranding, ProductStatus, TradeStatus


class AuditedAdmin(admin.ModelAdmin):
    audit_fields = ('create_by', 'modified_by')

    def get_readonly_fields(self, request, obj=None):
        excluded = self.get_exclude(request, obj) or ()
        return [
            field.name for field in self.opts.concrete_fields
            if field.name not in excluded and (not field.editable or field.name in self.audit_fields)
        ]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.create_by = request.user.get_username()
        obj.modified_by = request.user.get_username()
        super().save_model(request, obj, form, change)


@admin.register(ProductStatus, TradeStatus)
class ProductStatusAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active']
    search_fields = ['name', 'code']
    list_filter = ['is_active']
    readonly_fields = ['created_at', 'updated_at', 'create_by', 'modified_by']

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        return fields + ['code'] if obj else fields

    def save_model(self, request, obj, form, change):
        if not change:
            obj.create_by = request.user.get_username()
        obj.modified_by = request.user.get_username()
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.code == 'draft')

    def delete_queryset(self, request, queryset):
        if queryset.filter(code='draft').exists():
            raise PermissionDenied('The default draft status cannot be deleted.')
        super().delete_queryset(request, queryset)


@admin.register(ERPBranding)
class ERPBrandingAdmin(admin.ModelAdmin):
    fields = ('has_custom_logo', 'updated_at', 'modified_by')
    list_display = fields
    readonly_fields = fields

    @admin.display(boolean=True, description='Custom logo')
    def has_custom_logo(self, obj):
        return obj.logo_revision is not None

    def get_queryset(self, request):
        return super().get_queryset(request).defer('logo')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
