from rest_framework import serializers

from warehouse.scope import warehouse_scope

from .models import FinancialAccount
from .services import available_payment_accounts


class PaymentAccountField(serializers.PrimaryKeyRelatedField):
    def validate_empty_values(self, data):
        if data is serializers.empty and self.parent.instance is not None:
            raise serializers.SkipField()
        return super().validate_empty_values(data)

    def get_queryset(self):
        request = self.context.get('request')
        if request is None:
            return FinancialAccount.objects.none()
        return available_payment_accounts(warehouse_scope(request.user))
