from django.db import transaction
from django.db.models import F, Q
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from .models import Department, Designation, Employee, EmployeeStatus, EmploymentType, HRMLookup
from .permissions import check_employee_user_link


LOOKUP_FIELDS = ['id', 'code', 'name', 'description', 'is_active', *AUDIT_FIELDS[1:]]


class HRMLookupSerializer(CatalogSerializer):
    def validate_code(self, value):
        if issubclass(self.Meta.model, HRMLookup):
            value = value.strip().lower()
        if self.instance and value != self.instance.code:
            raise serializers.ValidationError('The code cannot be changed after creation.')
        return value


class DepartmentSerializer(HRMLookupSerializer):
    class Meta:
        model = Department
        fields = LOOKUP_FIELDS
        read_only_fields = AUDIT_FIELDS


class DesignationSerializer(HRMLookupSerializer):
    class Meta:
        model = Designation
        fields = LOOKUP_FIELDS
        read_only_fields = AUDIT_FIELDS


class EmploymentTypeSerializer(HRMLookupSerializer):
    class Meta:
        model = EmploymentType
        fields = LOOKUP_FIELDS
        read_only_fields = AUDIT_FIELDS


class EmployeeStatusSerializer(HRMLookupSerializer):
    class Meta:
        model = EmployeeStatus
        fields = LOOKUP_FIELDS
        read_only_fields = AUDIT_FIELDS


class EmployeeSerializer(CatalogSerializer):
    image = serializers.ImageField(required=False, allow_null=True)
    employment_type = serializers.SlugRelatedField(
        slug_field='code', queryset=EmploymentType.objects.all(), required=False)
    status = serializers.SlugRelatedField(
        slug_field='code', queryset=EmployeeStatus.objects.all(), required=False)
    department_name = serializers.CharField(source='department.name', read_only=True, default=None)
    designation_name = serializers.CharField(source='designation.name', read_only=True, default=None)
    employment_type_name = serializers.CharField(source='employment_type.name', read_only=True)
    status_name = serializers.CharField(source='status.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default=None)

    def get_fields(self):
        fields = super().get_fields()
        if isinstance(self.instance, Employee) and self.instance.reports_to_id:
            # Preserve the existing historical manager during full form updates;
            # other archived employees cannot be assigned.
            fields['reports_to'].queryset = Employee.all_objects.filter(
                Q(is_archived=False) | Q(pk=self.instance.reports_to_id))
        return fields

    def validate_employee_code(self, value):
        return value.strip().upper()

    def validate_work_email(self, value):
        return value.strip().lower()

    def validate_user(self, value):
        request = self.context.get('request')
        check_employee_user_link(request.user if request else None, self.instance, value)
        return value

    @transaction.atomic
    def update(self, instance, validated_data):
        # Claim the row before reloading it, including on SQLite. A stale update
        # must never save is_archived=False over a concurrent archive.
        if not Employee.objects.filter(pk=instance.pk).update(updated_at=F('updated_at')):
            raise NotFound('Employee not found.')
        self.instance = Employee.objects.select_for_update().get(pk=instance.pk)
        if 'user' in validated_data:
            self.validate_user(validated_data['user'])
        return super().update(self.instance, self.validate(validated_data))

    class Meta:
        model = Employee
        fields = [
            'id', 'employee_code', 'full_name', 'image', 'work_email', 'work_phone',
            'department', 'department_name', 'designation', 'designation_name',
            'employment_type', 'employment_type_name', 'status', 'status_name',
            'joining_date', 'leaving_date', 'reports_to', 'workplace', 'warehouse', 'warehouse_name', 'user',
            'is_archived', 'archived_at', 'archived_by', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'is_archived', 'archived_at', 'archived_by']


class EmployeeDetailSerializer(EmployeeSerializer):
    reports_to_name = serializers.SerializerMethodField()
    user_username = serializers.CharField(source='user.username', read_only=True, default=None)

    def get_reports_to_name(self, obj):
        if obj.reports_to_id is None:
            return None
        # Historical manager references must not disclose archived employee data.
        return Employee.objects.filter(pk=obj.reports_to_id).values_list('full_name', flat=True).first()

    class Meta(EmployeeSerializer.Meta):
        fields = [*EmployeeSerializer.Meta.fields, 'reports_to_name', 'user_username']


class EmployeeFilterSerializer(serializers.Serializer):
    department = serializers.IntegerField(min_value=1, required=False)
    designation = serializers.IntegerField(min_value=1, required=False)
    warehouse = serializers.IntegerField(min_value=1, required=False)
    employment_type = serializers.CharField(required=False)
    status = serializers.CharField(required=False)
