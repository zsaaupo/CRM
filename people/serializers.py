from decimal import Decimal
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from warehouse.models import WareHouse
from .models import Biller, BillerWarehouse, CustomerGroup, Lead, Party


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
    customer_group_name = serializers.CharField(source='customer_group.name', read_only=True, default=None)
    discount_plan_name = serializers.CharField(source='discount_plan.name', read_only=True, default=None)
    deposited_balance = serializers.DecimalField(source='deposit_balance', max_digits=18, decimal_places=4, read_only=True)
    total_due = serializers.SerializerMethodField()
    purchase_due = serializers.SerializerMethodField()
    return_due = serializers.SerializerMethodField()

    def validate_code(self, value):
        return value.strip().upper()

    def _due_cache(self, obj):
        cache = self.context.setdefault('_party_due_cache', {})
        if obj.pk in cache:
            return cache[obj.pk]
        if not obj.pk:
            res = {'customer_due': '0.0000', 'supplier_due': '0.0000', 'return_due': '0.0000'}
            cache[obj.pk] = res
            return res
        from rest_framework.exceptions import PermissionDenied
        from accounting.services import party_due_summary
        from warehouse.scope import warehouse_scope
        req = self.context.get('request')
        scope = None
        if req and getattr(req, 'user', None) and req.user.is_authenticated:
            try:
                scope = warehouse_scope(req.user)
            except PermissionDenied:
                scope = 'NO_SCOPE'
        if scope == 'NO_SCOPE':
            res = {'customer_due': '0.0000', 'supplier_due': '0.0000', 'return_due': '0.0000'}
        else:
            s = party_due_summary(obj.pk, scope=scope)
            ret_tot = sum((Decimal(str(d.get('returned', '0'))) for d in s.get('receivables', {}).get('documents', [])), Decimal('0'))
            res = {
                'customer_due': str(s.get('receivables', {}).get('total_due', '0.0000')),
                'supplier_due': str(s.get('payables', {}).get('total_due', '0.0000')),
                'return_due': f'{ret_tot:.4f}',
            }
        cache[obj.pk] = res
        return res

    def get_total_due(self, obj):
        return self._due_cache(obj)['customer_due'] if obj.is_customer else '0.0000'

    def get_purchase_due(self, obj):
        return self._due_cache(obj)['supplier_due'] if obj.is_supplier else '0.0000'

    def get_return_due(self, obj):
        return self._due_cache(obj)['return_due']

    class Meta:
        model = Party
        fields = [
            'id', 'code', 'name', 'image', 'is_customer', 'is_supplier', 'customer_group', 'customer_group_name',
            'discount_plan', 'discount_plan_name', 'reward_points', 'deposited_balance',
            'total_due', 'purchase_due', 'return_due', 'user',
            'contact_person', 'email', 'phone', 'alternate_phone', 'website', 'tax_number', 'address',
            'city', 'state', 'postal_code', 'country', 'notes', 'is_active',
            'is_deleted', 'deleted_at', 'deleted_by', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [
            *AUDIT_FIELDS, 'deposited_balance', 'reward_points', 'total_due', 'purchase_due',
            'return_due', 'user', 'is_deleted', 'deleted_at', 'deleted_by',
        ]


class CustomerGroupSerializer(CatalogSerializer):
    class Meta:
        model = CustomerGroup
        fields = ['id', 'name', 'percentage', 'price_basis', 'is_active', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class BillerSerializer(CatalogSerializer):
    image = serializers.ImageField(required=False, allow_null=True)
    warehouses = serializers.PrimaryKeyRelatedField(
        many=True, queryset=WareHouse.objects.filter(is_active=True), write_only=True, required=False,
    )
    warehouse_ids = serializers.SerializerMethodField()

    class Meta:
        model = Biller
        fields = [
            'id', 'name', 'company_name', 'image', 'vat_number', 'email', 'phone', 'address',
            'city', 'state', 'postal_code', 'country', 'is_active', 'user',
            'warehouses', 'warehouse_ids', 'is_deleted', 'deleted_at', 'deleted_by', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'warehouse_ids', 'is_deleted', 'deleted_at', 'deleted_by']

    def get_warehouse_ids(self, obj):
        return list(obj.warehouse_links.values_list('warehouse_id', flat=True))

    def validate(self, attrs):
        warehouses = attrs.pop('warehouses', None)
        attrs = super().validate(attrs)
        if warehouses is not None:
            attrs['warehouses'] = warehouses
        return attrs

    def create(self, validated_data):
        warehouses = validated_data.pop('warehouses', [])
        biller = super().create(validated_data)
        for wh in warehouses:
            BillerWarehouse.objects.get_or_create(
                biller=biller, warehouse=wh,
                defaults={'create_by': biller.create_by, 'modified_by': biller.modified_by},
            )
        return biller

    def update(self, instance, validated_data):
        warehouses = validated_data.pop('warehouses', None)
        biller = super().update(instance, validated_data)
        if warehouses is not None:
            BillerWarehouse.objects.filter(biller=biller).exclude(warehouse__in=warehouses).delete()
            for wh in warehouses:
                BillerWarehouse.objects.get_or_create(
                    biller=biller, warehouse=wh,
                    defaults={'create_by': biller.modified_by, 'modified_by': biller.modified_by},
                )
        return biller


class PartyFilterSerializer(serializers.Serializer):
    is_customer = serializers.BooleanField(required=False)
    is_supplier = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)
