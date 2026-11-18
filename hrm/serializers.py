from django.db import transaction
from django.db.models import F, Q
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from .models import (
    Attendance, Department, Designation, Employee, EmployeeAttendanceStatus, EmployeeScheduleAssignment,
    EmployeeStatus, EmploymentType, Holiday, HRMLookup, LeaveRequest, LeaveType, WorkSchedule,
)
from .permissions import REVIEW_LEAVE_PERMISSION, check_employee_user_link


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


class WorkScheduleSerializer(HRMLookupSerializer):
    class Meta:
        model = WorkSchedule
        fields = [*LOOKUP_FIELDS, 'working_days', 'start_time', 'end_time', 'break_minutes', 'grace_minutes']
        read_only_fields = AUDIT_FIELDS


class EmployeeAttendanceStatusSerializer(HRMLookupSerializer):
    class Meta:
        model = EmployeeAttendanceStatus
        fields = LOOKUP_FIELDS
        read_only_fields = AUDIT_FIELDS


class EmployeeAttendanceRecordSerializer(CatalogSerializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    employee_name = serializers.SerializerMethodField()

    def get_fields(self):
        fields = super().get_fields()
        if isinstance(self.instance, self.Meta.model):
            fields['employee'].queryset = Employee.all_objects.filter(
                Q(is_archived=False) | Q(pk=self.instance.employee_id))
        return fields

    def get_employee_name(self, obj):
        return None if obj.employee.is_archived else obj.employee.full_name

    def lock_employee(self, employee_id, *, allow_archived=False):
        employees = Employee.all_objects if allow_archived else Employee.objects
        # Claim a write lock on SQLite too. Schedule overlap checks must run after this lock.
        if not employees.filter(pk=employee_id).update(updated_at=F('updated_at')):
            raise serializers.ValidationError({'employee': ['Choose an employee who is not archived.']})

    @transaction.atomic
    def create(self, validated_data):
        self.lock_employee(validated_data['employee'].pk)
        return super().create(self.validate(validated_data))

    @transaction.atomic
    def update(self, instance, validated_data):
        rows = self.Meta.model.objects.filter(pk=instance.pk)
        if not rows.update(updated_at=F('updated_at')):
            raise NotFound('Record not found.')
        self.instance = rows.get()
        employee = validated_data.get('employee')
        employee_id = employee.pk if employee else self.instance.employee_id
        self.lock_employee(employee_id, allow_archived=employee_id == self.instance.employee_id)
        return super().update(self.instance, self.validate(validated_data))


class EmployeeScheduleAssignmentSerializer(EmployeeAttendanceRecordSerializer):
    schedule_name = serializers.CharField(source='schedule.name', read_only=True)

    class Meta:
        model = EmployeeScheduleAssignment
        fields = ['id', 'employee', 'employee_name', 'schedule', 'schedule_name', 'effective_from', 'effective_to',
                  *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS


class HolidaySerializer(CatalogSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, default=None)

    class Meta:
        model = Holiday
        fields = ['id', 'name', 'date', 'warehouse', 'warehouse_name', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS
        # Model validation handles both scoped uniqueness constraints and omitted warehouses.
        validators = []


class AttendanceSerializer(EmployeeAttendanceRecordSerializer):
    status = serializers.SlugRelatedField(slug_field='code', queryset=EmployeeAttendanceStatus.objects.all(),
                                          required=False)
    status_name = serializers.CharField(source='status.name', read_only=True)

    class Meta:
        model = Attendance
        fields = ['id', 'employee', 'employee_name', 'work_date', 'check_in', 'check_out', 'status', 'status_name',
                  'notes', *AUDIT_FIELDS[1:]]
        read_only_fields = AUDIT_FIELDS
        # Keep the model's work date default and validate uniqueness against the merged record.
        validators = []


class AttendanceDateFilterSerializer(serializers.Serializer):
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)

    def validate(self, attrs):
        if 'from_date' in attrs and 'to_date' in attrs and attrs['to_date'] < attrs['from_date']:
            raise serializers.ValidationError({'to_date': 'The end date cannot be before the start date.'})
        return attrs


class AttendanceFilterSerializer(AttendanceDateFilterSerializer):
    employee = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)
    status = serializers.SlugField(max_length=32, required=False)


class EmployeeScheduleAssignmentFilterSerializer(AttendanceDateFilterSerializer):
    employee = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)
    schedule = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)


class HolidayFilterSerializer(AttendanceDateFilterSerializer):
    warehouse = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)


class LeaveTypeSerializer(HRMLookupSerializer):
    class Meta:
        model = LeaveType
        fields = [*LOOKUP_FIELDS, 'is_paid']
        read_only_fields = AUDIT_FIELDS


class LeaveRequestSerializer(CatalogSerializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all(), required=False)
    employee_name = serializers.SerializerMethodField()
    leave_type = serializers.PrimaryKeyRelatedField(queryset=LeaveType.objects.filter(is_active=True))
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    status_name = serializers.CharField(source='get_status_display', read_only=True)

    def get_employee_name(self, obj):
        return None if obj.employee.is_archived else obj.employee.full_name

    def validate(self, attrs):
        user = self.context['request'].user
        linked = Employee.objects.filter(user_id=user.pk)
        employee = attrs.get('employee')
        if not user.has_perm(REVIEW_LEAVE_PERMISSION):
            if employee is not None and not linked.filter(pk=employee.pk).exists():
                raise PermissionDenied('You can only apply for your own leave.')
            employee = linked.first()
            if employee is None:
                raise PermissionDenied('Your login must be linked to an employee to apply for leave.')
        elif employee is None:
            employee = linked.first()
            if employee is None:
                raise serializers.ValidationError({'employee': 'Choose the employee applying for leave.'})
        attrs['employee'] = employee
        return super().validate(attrs)

    @transaction.atomic
    def create(self, validated_data):
        employee = Employee.objects.filter(pk=validated_data['employee'].pk)
        user = self.context['request'].user
        if not user.has_perm(REVIEW_LEAVE_PERMISSION):
            employee = employee.filter(user_id=user.pk)
        if not employee.update(updated_at=F('updated_at')):
            raise serializers.ValidationError({'employee': 'This employee is no longer available for this request.'})
        return super().create(validated_data)

    class Meta:
        model = LeaveRequest
        fields = ['id', 'employee', 'employee_name', 'leave_type', 'leave_type_name', 'start_date', 'end_date',
                  'reason', 'status', 'status_name', 'reviewed_by', 'reviewed_at', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'status', 'reviewed_by', 'reviewed_at']


class LeaveReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.REJECTED])


class LeaveRequestFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=LeaveRequest.Status.choices, required=False)
