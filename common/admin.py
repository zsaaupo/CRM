from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import (
    AuditLog, BackupSetting, BarcodeSetting, CustomField, CustomFieldValue,
    GeneralSetting, HRMSetting, IdempotencyRecord, ImportJob, Language, MailSetting,
    Notification, NotificationRecipient, OutboxMessage, POSSetting, PaymentAttempt,
    PaymentGatewayConfig, ProductStatus, RestaurantTable, RewardSetting, SMSSetting,
    SMSTemplate, TaskSetting, TradeStatus,
)


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


@admin.register(GeneralSetting)
class GeneralSettingAdmin(AuditedAdmin):
    list_display = ['system_title', 'company_name', 'costing_method', 'decimal_digits']

    def has_add_permission(self, request):
        return not GeneralSetting.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ['scope', 'key', 'result_type', 'result_id', 'created_at']
    search_fields = ['scope', 'key', 'result_type']
    readonly_fields = ['key', 'scope', 'payload_hash', 'result_type', 'result_id', 'result_data', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'module', 'action', 'model_name', 'object_id', 'actor']
    list_filter = ['module', 'action']
    search_fields = ['model_name', 'object_id', 'object_repr']
    readonly_fields = [
        'actor', 'action', 'module', 'model_name', 'object_id', 'object_repr',
        'warehouse', 'changes', 'request_key', 'ip_address', 'user_agent', 'created_at',
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = ['kind', 'status', 'created_by', 'request_key', 'created_at']
    list_filter = ['kind', 'status']
    search_fields = ['kind', 'request_key']
    readonly_fields = [
        'kind', 'file', 'status', 'dry_run_result', 'committed_result',
        'created_by', 'request_key', 'created_at', 'updated_at',
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register([
    POSSetting, HRMSetting, RewardSetting, TaskSetting,
    Notification, NotificationRecipient, SMSTemplate, MailSetting, SMSSetting,
    CustomField, CustomFieldValue, RestaurantTable, BarcodeSetting, Language,
    PaymentGatewayConfig, BackupSetting,
], AuditedAdmin)


@admin.register(OutboxMessage, PaymentAttempt)
class ReadOnlyOpsPostedAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
