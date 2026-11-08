from django.contrib import admin

from .models import Account


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    fields = [
        "user",
        "full_name",
        "email",
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
