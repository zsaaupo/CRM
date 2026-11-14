from copy import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import TradeStatus


AUDIT_FIELDS = ['id', 'created_at', 'updated_at', 'create_by', 'modified_by']


class CatalogSerializer(serializers.ModelSerializer):
    """Run model business rules for both new records and merged partial updates."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        candidate = copy(self.instance) if self.instance else self.Meta.model()
        for name, value in attrs.items():
            setattr(candidate, name, value)
        try:
            candidate.full_clean(exclude=['created_at', 'updated_at', 'create_by', 'modified_by'])
        except DjangoValidationError as exc:
            errors = exc.message_dict if hasattr(exc, 'message_dict') else {'__all__': exc.messages}
            raise serializers.ValidationError({
                'non_field_errors' if name == '__all__' else name: messages
                for name, messages in errors.items()
            }) from exc
        for name in ('sku', 'name'):
            if name in attrs:
                attrs[name] = getattr(candidate, name)
        return attrs


class TradeStatusSerializer(CatalogSerializer):
    def validate_code(self, value):
        if self.instance and value != self.instance.code:
            raise serializers.ValidationError('The code cannot be changed after creation.')
        return value

    class Meta:
        model = TradeStatus
        fields = ['id', 'name', 'code', 'description', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS
