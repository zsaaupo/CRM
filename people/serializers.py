from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from .models import Party


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
            'id', 'code', 'name', 'image', 'is_customer', 'is_supplier', 'contact_person',
            'email', 'phone', 'alternate_phone', 'website', 'tax_number', 'address',
            'city', 'state', 'postal_code', 'country', 'notes', 'is_active',
            'is_deleted', 'deleted_at', 'deleted_by', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'is_deleted', 'deleted_at', 'deleted_by']


class PartyFilterSerializer(serializers.Serializer):
    is_customer = serializers.BooleanField(required=False)
    is_supplier = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)
