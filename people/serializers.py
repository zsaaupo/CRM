from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from .models import Lead, Party


class LeadSerializer(CatalogSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.username', default=None, read_only=True)

    class Meta:
        model = Lead
        fields = [
            'id', 'name', 'company', 'job_title', 'email', 'phone', 'source', 'status', 'status_label',
            'expected_value', 'assigned_to', 'assigned_to_name', 'follow_up_date', 'expected_close_date',
            'notes', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = AUDIT_FIELDS

    def validate_assigned_to(self, user):
        if user and not user.is_active and user.pk != getattr(self.instance, 'assigned_to_id', None):
            raise serializers.ValidationError('Select an active user.')
        return user


class PartySerializer(CatalogSerializer):
    image = serializers.ImageField(required=False, allow_null=True)
    code = serializers.CharField(max_length=40, validators=[
        UniqueValidator(queryset=Party.all_objects.all(), lookup='iexact'),
    ])

    def validate_code(self, value):
        return value.strip().upper()

    class Meta:
        model = Party
        fields = [
            'id', 'code', 'name', 'image', 'is_customer', 'is_supplier', 'is_biller', 'contact_person',
            'email', 'phone', 'alternate_phone', 'website', 'tax_number', 'address',
            'city', 'state', 'postal_code', 'country', 'notes', 'is_active',
            'is_deleted', 'deleted_at', 'deleted_by', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'is_deleted', 'deleted_at', 'deleted_by']


class PartyFilterSerializer(serializers.Serializer):
    is_customer = serializers.BooleanField(required=False)
    is_supplier = serializers.BooleanField(required=False)
    is_biller = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)
