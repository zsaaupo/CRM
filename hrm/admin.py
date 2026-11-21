from uuid import uuid4

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError

from common.admin import AuditedAdmin

from .models import (
    Attendance, Department, Designation, Employee, EmployeeAttendanceStatus, EmployeeCompensation, EmployeeLoan,
    EmployeeScheduleAssignment, EmployeeStatus, EmploymentType, Holiday, LeaveRequest, LeaveType, LoanInstallment,
    PayrollAdjustment, PayrollDeductionRule, PayrollEvent, PayrollPeriod, PayrollRun, Payslip, PayslipLine,
    SalaryComponent, SalaryPayment, SalaryStructure, SalaryStructureLine, WorkSchedule,
)
from .permissions import REVIEW_LEAVE_PERMISSION, check_employee_user_link, leave_requests_for_user


@admin.register(Department, Designation, EmploymentType, EmployeeStatus)
class HRMLookupAdmin(AuditedAdmin):
    list_display = ['code', 'name', 'is_active']
    search_fields = ['code', 'name', 'description']
    list_filter = ['is_active']

    def get_readonly_fields(self, request, obj=None):
        return [*super().get_readonly_fields(request, obj), *(['code'] if obj else [])]


class EmployeeAdminForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = '__all__'

    def clean_user(self):
        user = self.cleaned_data.get('user')
        check_employee_user_link(self.actor, self.instance, user)
        return user


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    form = EmployeeAdminForm
    list_display = ['employee_code', 'full_name', 'department', 'designation', 'status', 'warehouse',
                    'is_archived', 'archived_at', 'archived_by']
    search_fields = ['employee_code', 'full_name', 'work_email', 'work_phone']
    list_filter = ['is_archived', 'status', 'employment_type', 'department', 'warehouse']
    list_select_related = ['department', 'designation', 'status', 'warehouse']
    readonly_fields = ['created_at', 'updated_at', 'create_by', 'modified_by',
                       'is_archived', 'archived_at', 'archived_by']

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # A fresh form class is produced per request; do not put actors on the shared base form.
        form.actor = request.user
        return form

    def get_readonly_fields(self, request, obj=None):
        if not request.user.has_perm('auth.change_user'):
            return [*self.readonly_fields, 'user']
        return self.readonly_fields

    def get_queryset(self, request):
        queryset = self.model.all_objects.all()
        ordering = self.get_ordering(request)
        return queryset.order_by(*ordering) if ordering else queryset

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_archived:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_archived:
            return False
        return super().has_delete_permission(request, obj)

    def get_deleted_objects(self, objs, request):
        employees = [obj for obj in objs if not obj.is_archived]
        permissions = set()
        if any(not self.has_delete_permission(request, obj) for obj in employees):
            permissions.add(str(self.opts.verbose_name))
        return ([str(obj) for obj in employees], {str(self.opts.verbose_name_plural): len(employees)}, permissions, [])

    def save_model(self, request, obj, form, change):
        if not change:
            obj.create_by = request.user.get_username()
        obj.modified_by = request.user.get_username()
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        obj.delete(actor=request.user.get_username())

    def delete_queryset(self, request, queryset):
        queryset.delete(actor=request.user.get_username())


@admin.register(WorkSchedule)
class WorkScheduleAdmin(HRMLookupAdmin):
    list_display = ['code', 'name', 'start_time', 'end_time', 'is_active']


@admin.register(EmployeeAttendanceStatus)
class EmployeeAttendanceStatusAdmin(HRMLookupAdmin):
    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (
            obj is None or obj.code != EmployeeAttendanceStatus.Code.UNRECORDED)

    def delete_queryset(self, request, queryset):
        if queryset.filter(code=EmployeeAttendanceStatus.Code.UNRECORDED).exists():
            raise PermissionDenied('The default attendance status cannot be deleted.')
        super().delete_queryset(request, queryset)


@admin.register(EmployeeScheduleAssignment)
class EmployeeScheduleAssignmentAdmin(AuditedAdmin):
    list_display = ['employee', 'schedule', 'effective_from', 'effective_to']
    search_fields = ['employee__employee_code', 'employee__full_name', 'schedule__name']
    list_select_related = ['employee', 'schedule']


@admin.register(Holiday)
class HolidayAdmin(AuditedAdmin):
    list_display = ['name', 'date', 'warehouse']
    search_fields = ['name', 'warehouse__name']
    list_filter = ['date']
    list_select_related = ['warehouse']


@admin.register(Attendance)
class AttendanceAdmin(AuditedAdmin):
    list_display = ['employee', 'work_date', 'status', 'check_in', 'check_out']
    search_fields = ['employee__employee_code', 'employee__full_name', 'notes']
    list_filter = ['work_date', 'status']
    list_select_related = ['employee', 'status']


@admin.register(LeaveType)
class LeaveTypeAdmin(HRMLookupAdmin):
    list_display = ['code', 'name', 'is_paid', 'is_active']
    list_filter = ['is_paid', 'is_active']


class LeaveRequestAdminForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get('employee')
        review_fields = {'status', 'reviewed_by', 'reviewed_at'}
        if employee and employee.user_id == self.actor.pk and review_fields.intersection(self.changed_data):
            self.add_error(None, 'You cannot review your own leave request.')
        return cleaned_data


@admin.register(LeaveRequest)
class LeaveRequestAdmin(AuditedAdmin):
    form = LeaveRequestAdminForm
    list_display = ['employee', 'leave_type', 'start_date', 'end_date', 'status']
    search_fields = ['employee__employee_code', 'employee__full_name', 'leave_type__name']
    list_filter = ['status', 'start_date']
    list_select_related = ['employee', 'leave_type']

    def get_queryset(self, request):
        return leave_requests_for_user(super().get_queryset(request), request.user)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.actor = request.user
        return form

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if not request.user.has_perm(REVIEW_LEAVE_PERMISSION) or (obj and obj.employee.user_id == request.user.pk):
            fields.extend(['status', 'reviewed_by', 'reviewed_at'])
        return fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'employee' and not request.user.has_perm(REVIEW_LEAVE_PERMISSION):
            kwargs['queryset'] = Employee.objects.filter(user=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(SalaryComponent)
class SalaryComponentAdmin(HRMLookupAdmin):
    list_display = ['code', 'name', 'type', 'category', 'is_active']
    search_fields = ['code', 'name', 'category']
    list_filter = ['type', 'is_active']


@admin.register(SalaryStructure)
class SalaryStructureAdmin(HRMLookupAdmin):
    list_display = ['code', 'name', 'currency', 'is_active']
    search_fields = ['code', 'name']
    list_filter = ['is_active']
    list_select_related = ['currency']


@admin.register(SalaryStructureLine)
class SalaryStructureLineAdmin(AuditedAdmin):
    list_display = ['salary_structure', 'component', 'calculation_method', 'fixed_amount', 'percentage']
    search_fields = ['salary_structure__name', 'component__name']
    list_filter = ['calculation_method']
    list_select_related = ['salary_structure', 'component']


@admin.register(EmployeeCompensation)
class EmployeeCompensationAdmin(AuditedAdmin):
    list_display = ['employee', 'salary_structure', 'basic_salary', 'effective_from', 'effective_to']
    search_fields = ['employee__employee_code', 'employee__full_name', 'salary_structure__name']
    list_select_related = ['employee', 'salary_structure']


class PayrollMonthInput(forms.DateInput):
    input_type = 'month'

    def __init__(self):
        super().__init__(format='%Y-%m', attrs={
            'placeholder': 'YYYY-MM',
            'pattern': '[0-9]{4}-(0[1-9]|1[0-2])',
        })


class PayrollMonthField(forms.DateField):
    widget = PayrollMonthInput
    input_formats = ('%Y-%m',)
    default_error_messages = {'invalid': 'Enter a payroll month in YYYY-MM format.'}


class PayrollMonthAdminForm(forms.ModelForm):
    month = PayrollMonthField(help_text='Select a payroll month (YYYY-MM).')


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(AuditedAdmin):
    form = PayrollMonthAdminForm
    list_display = ['month', 'created_at']
    list_filter = ['month']


@admin.register(PayrollRun)
class PayrollRunAdmin(AuditedAdmin):
    exclude = ['input_digest']
    list_display = ['period', 'currency', 'status', 'calculated_at', 'approved_at']
    search_fields = ['currency__code', 'calculated_by__username']
    list_filter = ['status']
    list_select_related = ['period', 'currency']


class PayrollRecordForm(forms.ModelForm):
    def validate_constraints(self):
        # Django normally omits constraints that depend on fields managed by payroll.
        excluded = self._get_validation_exclusions()
        excluded.difference_update(field.name for field in self.instance._meta.concrete_fields if not field.editable)
        try:
            self.instance.validate_constraints(exclude=excluded)
        except ValidationError as exc:
            self._update_errors(exc)


@admin.register(Payslip)
class PayslipAdmin(AuditedAdmin):
    list_display = ['employee_code', 'employee_name', 'payroll_run', 'gross_pay', 'total_deductions', 'net_pay']
    search_fields = ['employee_code', 'employee_name', 'department_name']
    list_select_related = ['payroll_run__period']

    def save_model(self, request, obj, form, change):
        if not change or 'employee' in form.changed_data:
            obj.employee_code = obj.employee.employee_code
            obj.employee_name = obj.employee.full_name
            obj.department_name = obj.employee.department.name if obj.employee.department_id else ''
        super().save_model(request, obj, form, change)


class PayslipLineCreateForm(PayrollRecordForm):
    line_code = forms.CharField(max_length=40, label='Code')
    line_name = forms.CharField(max_length=150, label='Name')
    line_type = forms.ChoiceField(choices=SalaryComponent.Kind.choices, label='Type')

    def clean(self):
        cleaned_data = super().clean()
        self.instance.code = cleaned_data.get('line_code', '')
        self.instance.name = cleaned_data.get('line_name', '')
        self.instance.type = cleaned_data.get('line_type', '')
        return cleaned_data

    def validate_constraints(self):
        if 'line_type' in self.cleaned_data:
            super().validate_constraints()


@admin.register(PayslipLine)
class PayslipLineAdmin(AuditedAdmin):
    form = PayrollRecordForm
    list_display = ['payslip', 'code', 'name', 'type', 'amount']
    search_fields = ['payslip__employee_code', 'payslip__employee_name', 'code', 'name']
    list_filter = ['type']
    list_select_related = ['payslip__payroll_run__period']

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs['form'] = PayslipLineCreateForm
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if obj else [field for field in fields if field not in ('code', 'name', 'type')]


@admin.register(PayrollEvent)
class PayrollEventAdmin(admin.ModelAdmin):
    readonly_fields = ['created_at']
    list_display = ['run', 'action', 'actor', 'created_at']
    search_fields = ['action', 'actor__username', 'note']
    list_select_related = ['run__period', 'actor']


@admin.register(SalaryPayment)
class SalaryPaymentAdmin(AuditedAdmin):
    form = PayrollRecordForm
    exclude = ['request_key']
    list_display = ['payslip', 'amount', 'method', 'status', 'reference', 'paid_on']
    search_fields = ['payslip__employee_code', 'payslip__employee_name', 'reference']
    list_filter = ['method', 'status']
    list_select_related = ['payslip__payroll_run__period']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.request_key = uuid4()
        super().save_model(request, obj, form, change)


@admin.register(PayrollAdjustment)
class PayrollAdjustmentAdmin(AuditedAdmin):
    form = PayrollRecordForm
    list_display = ['employee', 'component', 'effective_on', 'amount', 'currency', 'status']
    search_fields = ['employee__employee_code', 'employee__full_name', 'component__name', 'reason']
    list_filter = ['source', 'status']
    list_select_related = ['employee', 'component', 'currency']


class PayrollDeductionRuleAdminForm(forms.ModelForm):
    effective_from = PayrollMonthField(help_text='Select the first applicable payroll month (YYYY-MM).')
    effective_to = PayrollMonthField(required=False, help_text='Select the last applicable payroll month (YYYY-MM).')


@admin.register(PayrollDeductionRule)
class PayrollDeductionRuleAdmin(AuditedAdmin):
    form = PayrollDeductionRuleAdminForm
    list_display = ['salary_structure', 'component', 'method', 'value', 'effective_from', 'effective_to']
    search_fields = ['salary_structure__name', 'component__name', 'reference']
    list_filter = ['method']
    list_select_related = ['salary_structure', 'component']


@admin.register(EmployeeLoan)
class EmployeeLoanAdmin(AuditedAdmin):
    list_display = ['reference', 'employee', 'principal', 'currency', 'disbursed_on']
    search_fields = ['reference', 'employee__employee_code', 'employee__full_name']
    list_select_related = ['employee', 'currency']


@admin.register(LoanInstallment)
class LoanInstallmentAdmin(AuditedAdmin):
    form = PayrollMonthAdminForm
    list_display = ['loan', 'month', 'amount', 'recovered_at']
    search_fields = ['loan__reference', 'loan__employee__employee_code', 'loan__employee__full_name']
    list_filter = ['month']
    list_select_related = ['loan']
