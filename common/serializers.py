from copy import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import ImportJob, TradeStatus


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


class CustomFieldMixin(serializers.Serializer):
    """Persist and expose active OPS-03 CustomField values for supported entities."""

    custom_field_entity = ''
    custom_fields = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        cf_vals = attrs.pop('custom_fields', None)
        attrs = super().validate(attrs)
        if cf_vals is not None:
            attrs['_custom_fields'] = cf_vals
        return attrs

    def create(self, validated_data):
        cf_vals = validated_data.pop('_custom_fields', None)
        instance = super().create(validated_data)
        if self.custom_field_entity:
            from common.ops_services import validate_and_save_custom_fields
            validate_and_save_custom_fields(
                self.custom_field_entity, instance.pk, cf_vals or {}, is_create=True,
            )
        return instance

    def update(self, instance, validated_data):
        cf_vals = validated_data.pop('_custom_fields', None)
        instance = super().update(instance, validated_data)
        if self.custom_field_entity and cf_vals is not None:
            from common.ops_services import validate_and_save_custom_fields
            validate_and_save_custom_fields(
                self.custom_field_entity, instance.pk, cf_vals, is_create=False,
            )
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.custom_field_entity or not getattr(instance, 'pk', None):
            return data
        req = self.context.get('request') if hasattr(self, 'context') else None
        if req is None and not self.context.get('include_custom_fields'):
            return data
        from common.ops_services import get_custom_fields_for_object
        is_admin = bool(req and getattr(req, 'user', None) and req.user.is_superuser)
        rows = get_custom_fields_for_object(
            self.custom_field_entity, instance.pk, include_admin=is_admin,
        )
        data['custom_fields'] = {r['key']: r['value'] for r in rows}
        return data


class TradeStatusSerializer(CatalogSerializer):
    def validate_code(self, value):
        if self.instance and value != self.instance.code:
            raise serializers.ValidationError('The code cannot be changed after creation.')
        return value

    class Meta:
        model = TradeStatus
        fields = ['id', 'name', 'code', 'description', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class ImportJobSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, default=None)

    class Meta:
        model = ImportJob
        fields = [
            'id', 'kind', 'status', 'dry_run_result', 'committed_result',
            'created_by', 'created_by_username', 'request_key', 'created_at', 'updated_at',
        ]
        read_only_fields = fields
