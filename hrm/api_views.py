from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import OperationalError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.generics import GenericAPIView, RetrieveAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from inventory.models import Currency
from warehouse.models import WareHouse

from .attendance_import import AttendanceCSVImportSerializer, attendance_csv_template
from .models import (
    Attendance, AttendancePolicy, Department, Designation, Employee, EmployeeAttendanceStatus, EmployeeScheduleAssignment,
    EmployeeCompensation, EmployeeStatus, EmploymentType, Holiday, LeaveRequest, LeaveType,
    SalaryComponent, SalaryStructure, SalaryStructureLine, WorkSchedule,
)
from .permissions import ADD_LEAVE_PERMISSION, REVIEW_LEAVE_PERMISSION, VIEW_LEAVE_PERMISSION, leave_requests_for_user
from .serializers import (
    AttendanceFilterSerializer, AttendancePolicySerializer, AttendanceSerializer, DepartmentSerializer, DesignationSerializer,
    EmployeeAttendanceStatusSerializer, EmployeeCompensationFilterSerializer, EmployeeCompensationSerializer,
    EmployeeDetailSerializer, EmployeeFilterSerializer,
    EmployeeScheduleAssignmentFilterSerializer, EmployeeScheduleAssignmentSerializer, EmployeeSerializer,
    EmployeeStatusSerializer, EmploymentTypeSerializer, HolidayFilterSerializer, HolidaySerializer,
    LeaveRequestFilterSerializer, LeaveRequestSerializer, LeaveReviewSerializer, LeaveTypeSerializer,
    SalaryComponentSerializer, SalaryStructureLineFilterSerializer, SalaryStructureLineSerializer,
    SalaryStructureSerializer, WorkScheduleSerializer,
)
from .services import LeaveAlreadyReviewed, check_basic_salary_line_removal, lock_salary_record, review_leave_request


class HRMLookupQuerysetMixin:
    search_fields = ['code', 'name', 'description']


class DepartmentListCreateAPIView(HRMLookupQuerysetMixin, BaseListCreateAPIView):
    serializer_class = DepartmentSerializer
    queryset = Department.objects.all()
    response_key = 'department'


class DepartmentListAPIView(HRMLookupQuerysetMixin, BaseListAPIView):
    serializer_class = DepartmentSerializer
    queryset = Department.objects.all()


class DepartmentDetailAPIView(BaseDetailAPIView):
    serializer_class = DepartmentSerializer
    queryset = Department.objects.all()


class DepartmentUpdateAPIView(BaseUpdateAPIView):
    serializer_class = DepartmentSerializer
    queryset = Department.objects.all()


class DesignationListCreateAPIView(HRMLookupQuerysetMixin, BaseListCreateAPIView):
    serializer_class = DesignationSerializer
    queryset = Designation.objects.all()
    response_key = 'designation'


class DesignationListAPIView(HRMLookupQuerysetMixin, BaseListAPIView):
    serializer_class = DesignationSerializer
    queryset = Designation.objects.all()


class DesignationDetailAPIView(BaseDetailAPIView):
    serializer_class = DesignationSerializer
    queryset = Designation.objects.all()


class DesignationUpdateAPIView(BaseUpdateAPIView):
    serializer_class = DesignationSerializer
    queryset = Designation.objects.all()


class EmploymentTypeListCreateAPIView(HRMLookupQuerysetMixin, BaseListCreateAPIView):
    serializer_class = EmploymentTypeSerializer
    queryset = EmploymentType.objects.all()
    response_key = 'employment_type'


class EmploymentTypeListAPIView(HRMLookupQuerysetMixin, BaseListAPIView):
    serializer_class = EmploymentTypeSerializer
    queryset = EmploymentType.objects.all()


class EmploymentTypeDetailAPIView(BaseDetailAPIView):
    serializer_class = EmploymentTypeSerializer
    queryset = EmploymentType.objects.all()


class EmploymentTypeUpdateAPIView(BaseUpdateAPIView):
    serializer_class = EmploymentTypeSerializer
    queryset = EmploymentType.objects.all()


class EmployeeStatusListCreateAPIView(HRMLookupQuerysetMixin, BaseListCreateAPIView):
    serializer_class = EmployeeStatusSerializer
    queryset = EmployeeStatus.objects.all()
    response_key = 'employee_status'


class EmployeeStatusListAPIView(HRMLookupQuerysetMixin, BaseListAPIView):
    serializer_class = EmployeeStatusSerializer
    queryset = EmployeeStatus.objects.all()


class EmployeeStatusDetailAPIView(BaseDetailAPIView):
    serializer_class = EmployeeStatusSerializer
    queryset = EmployeeStatus.objects.all()


class EmployeeStatusUpdateAPIView(BaseUpdateAPIView):
    serializer_class = EmployeeStatusSerializer
    queryset = EmployeeStatus.objects.all()


class EmployeeQuerysetMixin:
    search_fields = ['employee_code', 'full_name', 'work_email', 'work_phone', 'department__name', 'designation__name']
    ordering = ['full_name', 'pk']

    def get_queryset(self):
        queryset = super().get_queryset()
        filters = EmployeeFilterSerializer(data=self.request.query_params.dict())
        filters.is_valid(raise_exception=True)
        return queryset.filter(**filters.validated_data)


def employee_queryset():
    return Employee.objects.select_related('department', 'designation', 'employment_type', 'status', 'warehouse', 'user')


class EmployeeListCreateAPIView(EmployeeQuerysetMixin, BaseListCreateAPIView):
    serializer_class = EmployeeSerializer
    queryset = employee_queryset()
    response_key = 'employee'


class EmployeeListAPIView(EmployeeQuerysetMixin, BaseListAPIView):
    serializer_class = EmployeeSerializer
    queryset = employee_queryset()


class EmployeeDetailAPIView(EmployeeQuerysetMixin, BaseDetailAPIView):
    serializer_class = EmployeeDetailSerializer
    queryset = employee_queryset()

    def perform_destroy(self, instance):
        instance.delete(actor=self.request.user.get_username())


class EmployeeUpdateAPIView(EmployeeQuerysetMixin, BaseUpdateAPIView):
    serializer_class = EmployeeSerializer
    queryset = employee_queryset()


@method_decorator(never_cache, name='dispatch')
class EmployeeOptionsAPIView(BaseAPIMixin, APIView):
    """Minimal choices for employee forms, without lookup management permissions."""
    required_permissions = ('hrm.add_employee',)
    http_method_names = ['get', 'head', 'options']
    employee = None

    def get(self, request):
        def choices(model, field, value_field='pk'):
            existing = getattr(self.employee, f'{field}_id', None)
            queryset = model.objects.filter(Q(is_active=True) | Q(**{value_field: existing}))
            return [{'value': row[value_field], 'label': row['name']}
                    for row in queryset.order_by('name', 'pk').values(value_field, 'name')]

        managers = Employee.objects.all()
        if self.employee:
            managers = managers.exclude(pk=self.employee.pk)
        manager_choices = [{'value': row['pk'], 'label': f"{row['full_name']} ({row['employee_code']})"}
                           for row in managers.order_by('full_name', 'pk').values('pk', 'full_name', 'employee_code')]
        if self.employee and self.employee.reports_to_id and not managers.filter(pk=self.employee.reports_to_id).exists():
            # Retain an existing relationship without disclosing an archived employee's identity.
            manager_choices.append({'value': self.employee.reports_to_id, 'label': 'Archived employee (existing)'})

        user_choices = []
        if request.user.has_perm('auth.change_user'):
            current_user = self.employee.user_id if self.employee else None
            users = get_user_model().objects.filter(Q(employee__isnull=True) | Q(pk=current_user))
            if not request.user.is_superuser:
                users = users.filter(is_superuser=False)
            user_choices = [{'value': user.pk, 'label': user.get_username()} for user in users.order_by('username', 'pk')]

        return Response({
            'department': choices(Department, 'department'),
            'designation': choices(Designation, 'designation'),
            'employment_type': choices(EmploymentType, 'employment_type', 'code'),
            'status': choices(EmployeeStatus, 'status', 'code'),
            'warehouse': choices(WareHouse, 'warehouse'),
            'reports_to': manager_choices, 'user': user_choices,
        })


class EmployeeEditOptionsAPIView(EmployeeOptionsAPIView):
    required_permissions = ('hrm.view_employee', 'hrm.change_employee')

    def get(self, request, pk):
        self.employee = get_object_or_404(Employee, pk=pk)
        return super().get(request)


class WorkScheduleResource:
    queryset = WorkSchedule.objects.all()
    serializer_class = WorkScheduleSerializer
    search_fields = ['code', 'name', 'description']


class WorkScheduleListCreateAPIView(WorkScheduleResource, BaseListCreateAPIView):
    response_key = 'work_schedule'


class WorkScheduleListAPIView(WorkScheduleResource, BaseListAPIView):
    pass


class WorkScheduleDetailAPIView(WorkScheduleResource, BaseDetailAPIView):
    pass


class WorkScheduleUpdateAPIView(WorkScheduleResource, BaseUpdateAPIView):
    pass


class AttendanceDateFilters:
    def get_queryset(self):
        queryset = super().get_queryset()
        filters = self.filter_serializer_class(data=self.request.query_params.dict())
        filters.is_valid(raise_exception=True)
        fields = dict(filters.validated_data)
        from_date = fields.pop('from_date', None)
        to_date = fields.pop('to_date', None)
        return self.filter_dates(queryset.filter(**fields), from_date, to_date)

    def filter_dates(self, queryset, from_date, to_date):
        if from_date:
            queryset = queryset.filter(**{f'{self.date_field}__gte': from_date})
        if to_date:
            queryset = queryset.filter(**{f'{self.date_field}__lte': to_date})
        return queryset


class EmployeeScheduleAssignmentResource:
    queryset = EmployeeScheduleAssignment.objects.select_related('employee', 'schedule')
    serializer_class = EmployeeScheduleAssignmentSerializer
    search_fields = ['schedule__code', 'schedule__name']
    ordering = ['employee_id', '-effective_from', '-pk']


class EffectiveDateFilters(AttendanceDateFilters):
    def filter_dates(self, queryset, from_date, to_date):
        if from_date:
            queryset = queryset.filter(Q(effective_to__isnull=True) | Q(effective_to__gte=from_date))
        if to_date:
            queryset = queryset.filter(effective_from__lte=to_date)
        return queryset


class EmployeeScheduleAssignmentListFilters(EffectiveDateFilters):
    filter_serializer_class = EmployeeScheduleAssignmentFilterSerializer


class EmployeeScheduleAssignmentListCreateAPIView(
        EmployeeScheduleAssignmentResource, EmployeeScheduleAssignmentListFilters, BaseListCreateAPIView):
    response_key = 'employee_schedule_assignment'


class EmployeeScheduleAssignmentListAPIView(
        EmployeeScheduleAssignmentResource, EmployeeScheduleAssignmentListFilters, BaseListAPIView):
    pass


class EmployeeScheduleAssignmentDetailAPIView(EmployeeScheduleAssignmentResource, BaseDetailAPIView):
    pass


class EmployeeScheduleAssignmentUpdateAPIView(EmployeeScheduleAssignmentResource, BaseUpdateAPIView):
    pass


class HolidayResource:
    queryset = Holiday.objects.select_related('warehouse')
    serializer_class = HolidaySerializer
    search_fields = ['name', 'warehouse__name', 'warehouse__code']
    ordering = ['date', 'name', 'pk']
    filter_serializer_class = HolidayFilterSerializer
    date_field = 'date'


class HolidayListCreateAPIView(HolidayResource, AttendanceDateFilters, BaseListCreateAPIView):
    response_key = 'holiday'


class HolidayListAPIView(HolidayResource, AttendanceDateFilters, BaseListAPIView):
    pass


class HolidayDetailAPIView(HolidayResource, BaseDetailAPIView):
    pass


class HolidayUpdateAPIView(HolidayResource, BaseUpdateAPIView):
    pass


class EmployeeAttendanceStatusResource:
    queryset = EmployeeAttendanceStatus.objects.all()
    serializer_class = EmployeeAttendanceStatusSerializer
    search_fields = ['code', 'name', 'description']


class EmployeeAttendanceStatusListCreateAPIView(EmployeeAttendanceStatusResource, BaseListCreateAPIView):
    response_key = 'employee_attendance_status'


class EmployeeAttendanceStatusListAPIView(EmployeeAttendanceStatusResource, BaseListAPIView):
    pass


class EmployeeAttendanceStatusDetailAPIView(EmployeeAttendanceStatusResource, BaseDetailAPIView):
    protected_codes = (EmployeeAttendanceStatus.Code.UNRECORDED,)


class EmployeeAttendanceStatusUpdateAPIView(EmployeeAttendanceStatusResource, BaseUpdateAPIView):
    pass


class AttendanceResource:
    queryset = Attendance.objects.select_related('employee', 'status')
    serializer_class = AttendanceSerializer
    search_fields = ['notes', 'status__code', 'status__name']
    ordering = ['-work_date', 'employee_id', '-pk']
    filter_serializer_class = AttendanceFilterSerializer
    date_field = 'work_date'


class AttendanceListCreateAPIView(AttendanceResource, AttendanceDateFilters, BaseListCreateAPIView):
    response_key = 'attendance'


@method_decorator(never_cache, name='dispatch')
class AttendanceImportAPIView(BaseAPIMixin, GenericAPIView):
    serializer_class = AttendanceCSVImportSerializer
    required_permissions = ('hrm.add_attendance',)
    parser_classes = [MultiPartParser]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        return attendance_csv_template()

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.save_record(serializer, actor=request.user)
        return Response({'message': 'Attendance imported successfully.', **serializer.instance},
                        status=status.HTTP_201_CREATED)


class AttendanceListAPIView(AttendanceResource, AttendanceDateFilters, BaseListAPIView):
    pass


class AttendanceDetailAPIView(AttendanceResource, BaseDetailAPIView):
    pass


class AttendanceUpdateAPIView(AttendanceResource, BaseUpdateAPIView):
    pass


class AttendancePolicyResource:
    queryset = AttendancePolicy.objects.select_related('penalty_leave_type')
    serializer_class = AttendancePolicySerializer
    search_fields = ['penalty_leave_type__name']
    ordering = ['-effective_from', '-pk']


class AttendancePolicyListCreateAPIView(AttendancePolicyResource, BaseListCreateAPIView):
    response_key = 'attendance_policy'


class AttendancePolicyListAPIView(AttendancePolicyResource, BaseListAPIView):
    pass


@method_decorator(never_cache, name='dispatch')
class AttendancePolicyDetailAPIView(AttendancePolicyResource, BaseAPIMixin, RetrieveAPIView):
    http_method_names = ['get', 'head', 'options']


class AttendancePolicyUpdateAPIView(AttendancePolicyResource, BaseUpdateAPIView):
    pass


@method_decorator(never_cache, name='dispatch')
class HRMFormOptionsAPIView(BaseAPIMixin, APIView):
    model = None
    http_method_names = ['get', 'head', 'options']

    @property
    def required_permissions(self):
        opts = self.model._meta
        actions = ('view', 'change') if 'pk' in self.kwargs else ('add',)
        return tuple(f'{opts.app_label}.{action}_{opts.model_name}' for action in actions)

    def get(self, request, pk=None):
        record = get_object_or_404(self.model, pk=pk) if pk is not None else None
        return Response(self.get_options(record))

    @staticmethod
    def employee_choices(record):
        employees = Employee.objects.order_by('full_name', 'pk').values_list('pk', 'full_name', 'employee_code')
        choices = [{'value': pk, 'label': f'{name} ({code})'} for pk, name, code in employees]
        if record and not any(choice['value'] == record.employee_id for choice in choices):
            choices.append({'value': record.employee_id, 'label': 'Archived employee (existing)'})
        return choices


class AttendanceFormOptionsAPIView(HRMFormOptionsAPIView):
    def get_options(self, record):
        options = {}

        def lookup_choices(model, field, value_field='pk', include=()):
            existing = getattr(record, f'{field}_id', None)
            queryset = model.objects.filter(Q(is_active=True) | Q(**{f'{value_field}__in': (*include, existing)}))
            return [{'value': value, 'label': name} for value, name in
                    queryset.order_by('name', 'pk').values_list(value_field, 'name')]

        if self.model in (Attendance, EmployeeScheduleAssignment):
            options['employee'] = self.employee_choices(record)
        if self.model is Attendance:
            options['status'] = lookup_choices(EmployeeAttendanceStatus, 'status', 'code',
                                              include=(EmployeeAttendanceStatus.Code.UNRECORDED,))
        elif self.model is EmployeeScheduleAssignment:
            options['schedule'] = lookup_choices(WorkSchedule, 'schedule')
        elif self.model is Holiday:
            options['warehouse'] = lookup_choices(WareHouse, 'warehouse')
        return options


class AttendancePolicyOptionsAPIView(HRMFormOptionsAPIView):
    model = AttendancePolicy

    def get_options(self, record):
        existing = record.penalty_leave_type_id if record else None
        leave_types = LeaveType.objects.filter(Q(is_active=True, is_paid=True) | Q(pk=existing))
        return {
            'penalty_leave_type': [{'value': pk, 'label': name} for pk, name in leave_types.values_list('pk', 'name')],
            'deduction_basis': [{'value': value, 'label': label} for value, label in AttendancePolicy.DeductionBasis.choices],
            'deduction_divisor': [{'value': value, 'label': label} for value, label in AttendancePolicy.DeductionDivisor.choices],
        }


class LeaveTypeResource(HRMLookupQuerysetMixin):
    queryset = LeaveType.objects.all()
    serializer_class = LeaveTypeSerializer


class LeaveTypeListCreateAPIView(LeaveTypeResource, BaseListCreateAPIView):
    response_key = 'leave_type'


class LeaveTypeListAPIView(LeaveTypeResource, BaseListAPIView):
    pass


class LeaveTypeDetailAPIView(LeaveTypeResource, BaseDetailAPIView):
    pass


class LeaveTypeUpdateAPIView(LeaveTypeResource, BaseUpdateAPIView):
    pass


class LeaveRequestResource:
    queryset = LeaveRequest.objects.select_related('employee', 'leave_type')
    serializer_class = LeaveRequestSerializer
    search_fields = ['leave_type__name', 'status']
    ordering = ['-start_date', '-pk']

    def get_queryset(self):
        return leave_requests_for_user(super().get_queryset(), self.request.user)


class LeaveRequestListFilters:
    def get_queryset(self):
        filters = LeaveRequestFilterSerializer(data=self.request.query_params.dict())
        filters.is_valid(raise_exception=True)
        return super().get_queryset().filter(**filters.validated_data)


class LeaveRequestListCreateAPIView(LeaveRequestListFilters, LeaveRequestResource, BaseListCreateAPIView):
    response_key = 'leave_request'


class LeaveRequestListAPIView(LeaveRequestListFilters, LeaveRequestResource, BaseListAPIView):
    pass


@method_decorator(never_cache, name='dispatch')
class LeaveRequestDetailAPIView(LeaveRequestResource, BaseAPIMixin, RetrieveAPIView):
    http_method_names = ['get', 'head', 'options']


@method_decorator(never_cache, name='dispatch')
class LeaveRequestOptionsAPIView(BaseAPIMixin, APIView):
    required_permissions = (ADD_LEAVE_PERMISSION,)
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        options = {}
        employees = Employee.objects.order_by('full_name', 'pk')
        if request.user.has_perm(REVIEW_LEAVE_PERMISSION):
            options['employee'] = [
                {'value': pk, 'label': f'{name} ({code})'}
                for pk, name, code in employees.values_list('pk', 'full_name', 'employee_code')
            ]
        elif not employees.filter(user_id=request.user.pk).exists():
            raise PermissionDenied('Your login must be linked to an employee to apply for leave.')
        options['leave_type'] = [
            {'value': pk, 'label': f"{name} ({'Paid' if paid else 'Unpaid'})"}
            for pk, name, paid in LeaveType.objects.filter(is_active=True).values_list('pk', 'name', 'is_paid')
        ]
        return Response(options)


class LeaveReviewConflict(APIException):
    status_code = 409
    default_detail = 'Only a pending leave request can be reviewed.'


@method_decorator(never_cache, name='dispatch')
class LeaveRequestReviewAPIView(BaseAPIMixin, APIView):
    required_permissions = (VIEW_LEAVE_PERMISSION, REVIEW_LEAVE_PERMISSION)
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        serializer = LeaveReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            leave = review_leave_request(pk, request.user, serializer.validated_data['status'])
        except LeaveRequest.DoesNotExist as exc:
            raise NotFound('Leave request not found.') from exc
        except LeaveAlreadyReviewed as exc:
            raise LeaveReviewConflict() from exc
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict) from exc
        return Response(LeaveRequestSerializer(leave).data)


class SalaryConfigurationBusy(APIException):
    status_code = 409
    default_detail = 'This salary configuration is being updated. Please retry.'
    default_code = 'salary_configuration_busy'


class SalaryConfigurationResource:
    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and ('locked' in str(exc).lower() or 'deadlock' in str(exc).lower()):
            exc = SalaryConfigurationBusy()
        return super().handle_exception(exc)


class SalaryComponentResource(SalaryConfigurationResource, HRMLookupQuerysetMixin):
    queryset = SalaryComponent.objects.all()
    serializer_class = SalaryComponentSerializer
    search_fields = [*HRMLookupQuerysetMixin.search_fields, 'type', 'category']


class SalaryComponentListCreateAPIView(SalaryComponentResource, BaseListCreateAPIView):
    response_key = 'salary_component'


class SalaryComponentListAPIView(SalaryComponentResource, BaseListAPIView):
    pass


class SalaryComponentDetailAPIView(SalaryComponentResource, BaseDetailAPIView):
    pass


class SalaryComponentUpdateAPIView(SalaryComponentResource, BaseUpdateAPIView):
    pass


class SalaryStructureResource(SalaryConfigurationResource, HRMLookupQuerysetMixin):
    queryset = SalaryStructure.objects.all()
    serializer_class = SalaryStructureSerializer


class SalaryStructureListCreateAPIView(SalaryStructureResource, BaseListCreateAPIView):
    response_key = 'salary_structure'


class SalaryStructureListAPIView(SalaryStructureResource, BaseListAPIView):
    pass


class SalaryStructureDetailAPIView(SalaryStructureResource, BaseDetailAPIView):
    pass


class SalaryStructureUpdateAPIView(SalaryStructureResource, BaseUpdateAPIView):
    pass


class SalaryStructureLineResource(SalaryConfigurationResource):
    queryset = SalaryStructureLine.objects.select_related('salary_structure', 'component')
    serializer_class = SalaryStructureLineSerializer
    search_fields = ['salary_structure__code', 'salary_structure__name', 'component__code', 'component__name']
    ordering = ['salary_structure_id', 'pk']


class SalaryStructureLineListFilters:
    def get_queryset(self):
        filters = SalaryStructureLineFilterSerializer(data=self.request.query_params.dict())
        filters.is_valid(raise_exception=True)
        return super().get_queryset().filter(**filters.validated_data)


class SalaryStructureLineListCreateAPIView(SalaryStructureLineResource, SalaryStructureLineListFilters, BaseListCreateAPIView):
    response_key = 'salary_structure_line'


class SalaryStructureLineListAPIView(SalaryStructureLineResource, SalaryStructureLineListFilters, BaseListAPIView):
    pass


class SalaryStructureLineDetailAPIView(SalaryStructureLineResource, BaseDetailAPIView):
    @transaction.atomic
    def perform_destroy(self, instance):
        line = lock_salary_record(SalaryStructureLine, instance.pk)
        lock_salary_record(SalaryStructure, line.salary_structure_id)
        check_basic_salary_line_removal(line)
        return super().perform_destroy(line)


class SalaryStructureLineUpdateAPIView(SalaryStructureLineResource, BaseUpdateAPIView):
    pass


class EmployeeCompensationResource(SalaryConfigurationResource):
    queryset = EmployeeCompensation.objects.select_related('employee', 'salary_structure')
    serializer_class = EmployeeCompensationSerializer
    search_fields = ['salary_structure__code', 'salary_structure__name']
    ordering = ['employee_id', '-effective_from', '-pk']


class EmployeeCompensationListFilters(EffectiveDateFilters):
    filter_serializer_class = EmployeeCompensationFilterSerializer


class EmployeeCompensationListCreateAPIView(EmployeeCompensationResource, EmployeeCompensationListFilters, BaseListCreateAPIView):
    response_key = 'employee_compensation'


class EmployeeCompensationListAPIView(EmployeeCompensationResource, EmployeeCompensationListFilters, BaseListAPIView):
    pass


class EmployeeCompensationDetailAPIView(EmployeeCompensationResource, BaseDetailAPIView):
    pass


class EmployeeCompensationUpdateAPIView(EmployeeCompensationResource, BaseUpdateAPIView):
    pass


class SalaryFormOptionsAPIView(HRMFormOptionsAPIView):
    def get_options(self, record):
        def lookup_choices(model, field, value_field='pk', eligibility=None):
            existing = getattr(record, f'{field}_id', None)
            available = Q(is_active=True) if eligibility is None else Q(is_active=True) & eligibility
            rows = model.objects.filter(available | Q(**{value_field: existing})).order_by('name', 'pk')
            return [{'value': value, 'label': f'{name} ({code})'}
                    for value, name, code in rows.values_list(value_field, 'name', 'code')]

        if self.model is SalaryComponent:
            return {'type': [{'value': value, 'label': label} for value, label in SalaryComponent.Kind.choices]}
        if self.model is SalaryStructure:
            return {'currency': lookup_choices(Currency, 'currency', 'code')}
        if self.model is SalaryStructureLine:
            return {
                'salary_structure': lookup_choices(SalaryStructure, 'salary_structure'),
                'component': lookup_choices(SalaryComponent, 'component'),
                'calculation_method': [{'value': value, 'label': label}
                                       for value, label in SalaryStructureLine.CalculationMethod.choices],
            }

        basic_lines = SalaryStructureLine.objects.filter(calculation_method=SalaryStructureLine.CalculationMethod.BASIC_SALARY)
        return {
            'employee': self.employee_choices(record),
            'salary_structure': lookup_choices(SalaryStructure, 'salary_structure',
                                               eligibility=Q(pk__in=basic_lines.values('salary_structure_id'))),
        }
