from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.generics import RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from warehouse.models import WareHouse

from .models import (
    Attendance, Department, Designation, Employee, EmployeeAttendanceStatus, EmployeeScheduleAssignment,
    EmployeeStatus, EmploymentType, Holiday, LeaveRequest, LeaveType, WorkSchedule,
)
from .permissions import ADD_LEAVE_PERMISSION, REVIEW_LEAVE_PERMISSION, VIEW_LEAVE_PERMISSION, leave_requests_for_user
from .serializers import (
    AttendanceFilterSerializer, AttendanceSerializer, DepartmentSerializer, DesignationSerializer,
    EmployeeAttendanceStatusSerializer, EmployeeDetailSerializer, EmployeeFilterSerializer,
    EmployeeScheduleAssignmentFilterSerializer, EmployeeScheduleAssignmentSerializer, EmployeeSerializer,
    EmployeeStatusSerializer, EmploymentTypeSerializer, HolidayFilterSerializer, HolidaySerializer,
    LeaveRequestFilterSerializer, LeaveRequestSerializer, LeaveReviewSerializer, LeaveTypeSerializer,
    WorkScheduleSerializer,
)
from .services import LeaveAlreadyReviewed, review_leave_request


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


class EmployeeScheduleAssignmentListFilters(AttendanceDateFilters):
    filter_serializer_class = EmployeeScheduleAssignmentFilterSerializer

    def filter_dates(self, queryset, from_date, to_date):
        if from_date:
            queryset = queryset.filter(Q(effective_to__isnull=True) | Q(effective_to__gte=from_date))
        if to_date:
            queryset = queryset.filter(effective_from__lte=to_date)
        return queryset


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


class AttendanceListAPIView(AttendanceResource, AttendanceDateFilters, BaseListAPIView):
    pass


class AttendanceDetailAPIView(AttendanceResource, BaseDetailAPIView):
    pass


class AttendanceUpdateAPIView(AttendanceResource, BaseUpdateAPIView):
    pass


@method_decorator(never_cache, name='dispatch')
class AttendanceFormOptionsAPIView(BaseAPIMixin, APIView):
    model = None
    http_method_names = ['get', 'head', 'options']

    @property
    def required_permissions(self):
        opts = self.model._meta
        actions = ('view', 'change') if 'pk' in self.kwargs else ('add',)
        return tuple(f'{opts.app_label}.{action}_{opts.model_name}' for action in actions)

    def get(self, request, pk=None):
        record = get_object_or_404(self.model, pk=pk) if pk is not None else None
        options = {}

        def lookup_choices(model, field, value_field='pk', include=()):
            existing = getattr(record, f'{field}_id', None)
            queryset = model.objects.filter(Q(is_active=True) | Q(**{f'{value_field}__in': (*include, existing)}))
            return [{'value': value, 'label': name} for value, name in
                    queryset.order_by('name', 'pk').values_list(value_field, 'name')]

        if self.model in (Attendance, EmployeeScheduleAssignment):
            employees = list(Employee.objects.order_by('full_name', 'pk').values_list('pk', 'full_name', 'employee_code'))
            options['employee'] = [{'value': value, 'label': f'{name} ({code})'} for value, name, code in employees]
            if record and not any(value == record.employee_id for value, _, _ in employees):
                options['employee'].append({'value': record.employee_id, 'label': 'Archived employee (existing)'})
        if self.model is Attendance:
            options['status'] = lookup_choices(EmployeeAttendanceStatus, 'status', 'code',
                                              include=(EmployeeAttendanceStatus.Code.UNRECORDED,))
        elif self.model is EmployeeScheduleAssignment:
            options['schedule'] = lookup_choices(WorkSchedule, 'schedule')
        elif self.model is Holiday:
            options['warehouse'] = lookup_choices(WareHouse, 'warehouse')
        return Response(options)


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
