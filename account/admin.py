from django.contrib import admin
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.models import User, Group

from .models import Account


class SuperuserWritesMixin:
    """Keep Django admin from bypassing the application's access-management rules."""

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class ProtectedUserAdmin(SuperuserWritesMixin, UserAdmin):
    pass


@admin.register(Group)
class ProtectedGroupAdmin(SuperuserWritesMixin, GroupAdmin):
    pass


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else [*fields, 'user', 'email']

    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)
    fields = [
        "user",
        "full_name",
        "email",
        "image",
        "otp",
        "otp_expires_at",
        "date_of_birth",
        "gender",
        "phone_number",
        "designation",
        "address",
        "city",
        "state",
        "postal_code",
        "country",
        "created_at",
        "updated_at",
    ]

    list_display = [
        "full_name",
        "email",
        "user",
        "phone_number",
        "designation",
        "country",
        "created_at",
        "updated_at",
    ]

    readonly_fields = ["otp", "otp_expires_at", "created_at", "updated_at"]
