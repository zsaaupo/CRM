from rest_framework import serializers

from warehouse.scope import warehouse_scope

from .models import FinancialAccount
from .services import available_payment_accounts


class OrderSettlementMixin:
    def validate(self, attrs):
        from common.models import SettlementMode

        attrs = super().validate(attrs)
        mode = attrs.get('settlement_mode', getattr(self.instance, 'settlement_mode', SettlementMode.AUTO_FULL))
        account = attrs.get('payment_account', getattr(self.instance, 'payment_account_id', None))
        changed = self.instance is None or 'settlement_mode' in attrs or 'payment_account' in attrs
        if changed and mode == SettlementMode.AUTO_FULL and not account:
            raise serializers.ValidationError({'payment_account': 'Choose an account for automatic payment.'})
        return attrs


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
