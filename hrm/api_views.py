from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.response import Response
from rest_framework.views import APIView

from common.view import BaseAPIMixin, BaseDetailAPIView, BaseListAPIView, BaseListCreateAPIView, BaseUpdateAPIView
from warehouse.models import WareHouse

from .models import Department, Designation, Employee, EmployeeStatus, EmploymentType
from .serializers import (
    DepartmentSerializer, DesignationSerializer, EmployeeDetailSerializer, EmployeeFilterSerializer,
    EmployeeSerializer, EmployeeStatusSerializer, EmploymentTypeSerializer,
)


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
