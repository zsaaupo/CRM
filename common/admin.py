from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import Status


@admin.register(Status)
class StatusAdmin(admin.ModelAdmin):
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
