from calendar import day_name
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from account.page_views import ProtectedAccountPageView
from .catalog import RESOURCE_MAP
from .models import Employee, EmployeeAttendanceStatus, LeaveRequest, PayrollAdjustment, PayrollRun, SalaryPayment
from .payroll import RECALCULABLE
from .payroll_workflow import RUN_ACTIONS
from .permissions import (PAYROLL_CALCULATE, PAYROLL_DETAIL, PAYROLL_REVIEW_ADJUSTMENT, REVIEW_LEAVE_PERMISSION,
                          can_apply_for_leave, leave_requests_for_user)


class HRMPageView(ProtectedAccountPageView):
    resource_key = None
    mode = 'list'

    @property
    def resource(self):
        if self.resource_key not in RESOURCE_MAP:
            raise ImproperlyConfigured('Declare a registered HRM resource.')
        return RESOURCE_MAP[self.resource_key]

    def get_permission_required(self):
        permissions = [self.resource.permission('view')]
        if self.mode == 'add':
            permissions.append(self.resource.permission('add'))
        elif self.mode == 'edit':
            permissions.append(self.resource.permission('change'))
        return permissions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        resource, user = self.resource, self.request.user
        context.update({
            'resource': resource, 'page_section': 'hrm', 'section_label': 'HRM',
            'hrm_key': resource.key, 'hrm_group': resource.group, 'page_mode': self.mode,
            'page_label': f'{resource.singular.capitalize()} list',
            'list_url': reverse(f'hrm_pages:{resource.key}-list'),
            'add_url': reverse(f'hrm_pages:add-{resource.key}'),
            'list_api_url': reverse(f'hrm:{resource.key}-list-view'),
            'can_add': user.has_perm(resource.permission('add')),
            'can_delete': user.has_perm(resource.permission('delete')),
            'list_description': f'Your {resource.plural}, in one place.',
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in resource.columns],
        })
        if resource.key == 'leave-request':
            context.update({
                'can_add': can_apply_for_leave(user), 'can_delete': False, 'add_label': 'Apply for leave',
                'view_url_template': reverse('hrm_pages:leave-request-detail', args=[0]),
            })
            context['columns'].append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
            return context
        if resource.key == 'payroll-run':
            context.update(can_add=user.has_perms(PAYROLL_CALCULATE), can_delete=False, add_label='Calculate payroll')
            if user.has_perms(PAYROLL_DETAIL):
                context['view_url_template'] = reverse('hrm_pages:payroll-run-detail', args=[0])
                context['columns'].append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
            return context
        if resource.key == 'employee':
            context.update({'soft_delete': True,
                            'soft_delete_description': 'The employee record will be retained.'})
        can_change = user.has_perm(resource.permission('change'))
        if resource.key in ('employee', 'payroll-adjustment') or can_change or context['can_delete']:
            context['columns'].append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
        if resource.key == 'employee':
            context['view_url_template'] = reverse('hrm_pages:employee-detail', args=[0])
        if resource.key == 'payroll-adjustment':
            context['view_url_template'] = reverse('hrm_pages:payroll-adjustment-detail', args=[0])
            context['edit_disabled_field'] = 'is_locked'
            context['delete_disabled_field'] = 'is_locked'
        if resource.key == 'loan-installment':
            context.update(edit_disabled_field='recovered', delete_disabled_field='recovered')
        if can_change:
            context['edit_url_template'] = reverse(f'hrm_pages:edit-{resource.key}', args=[0])
        if context['can_delete']:
            context['delete_url_template'] = reverse(f'hrm:{resource.key}-detail', args=[0])
        return context


class HRMListPageView(HRMPageView):
    template_name = 'inventory/list.html'


class PayrollCalculationPageView(HRMPageView):
    template_name = 'hrm/payroll_calculation.html'
    resource_key = 'payroll-run'
    mode = 'add'

    def get_permission_required(self):
        return PAYROLL_CALCULATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_label': 'Calculate payroll', 'preview_api_url': reverse('hrm:payroll-run-preview'),
            'calculate_api_url': reverse('hrm:payroll-run-calculate'),
            'options_api_url': reverse('hrm:payroll-run-options'),
            'detail_url_template': reverse('hrm_pages:payroll-run-detail', args=[0]),
            'initial_month': self.request.GET.get('month', timezone.localdate().strftime('%Y-%m')),
            'initial_currency': self.request.GET.get('currency', settings.INVENTORY_BASE_CURRENCY),
        })
        return context


class PayrollRunDetailPageView(HRMPageView):
    template_name = 'hrm/payroll_run_detail.html'
    resource_key = 'payroll-run'
    mode = 'detail'

    def get_permission_required(self):
        return PAYROLL_DETAIL

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run = get_object_or_404(PayrollRun.objects.select_related('period'), pk=self.kwargs['pk'])
        context.update(page_label='Payroll run', detail_api_url=reverse('hrm:payroll-run-detail', args=[run.pk]), run=run)
        context.update({
            'transition_urls': {key: reverse(f'hrm:payroll-run-{key}', args=[run.pk]) for key in RUN_ACTIONS},
            'create_payment_url': reverse('hrm:salary-payment-create', args=[run.pk]),
            'resolve_payment_template': reverse('hrm:salary-payment-resolve', args=[run.pk, 0]),
            'payslip_pdf_template': reverse('report:payslip-pdf', args=[0]),
            'payment_methods': SalaryPayment.Method.choices, 'today': timezone.localdate().isoformat(),
        })
        if context['can_add'] and run.status in RECALCULABLE:
            parameters = urlencode({'month': run.period.month.strftime('%Y-%m'), 'currency': run.currency_id})
            context['recalculate_url'] = f"{context['add_url']}?{parameters}"
        return context


class PayrollAdjustmentDetailPageView(HRMPageView):
    template_name = 'hrm/payroll_adjustment_detail.html'
    resource_key = 'payroll-adjustment'
    mode = 'detail'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        adjustment = get_object_or_404(PayrollAdjustment, pk=self.kwargs['pk'])
        context.update(adjustment=adjustment, page_label='Payroll adjustment',
            detail_api_url=reverse('hrm:payroll-adjustment-detail', args=[adjustment.pk]),
            review_api_url=reverse('hrm:payroll-adjustment-review', args=[adjustment.pk])
            if self.request.user.has_perms(PAYROLL_REVIEW_ADJUSTMENT) else '')
        return context


class PayrollReportPageView(ProtectedAccountPageView):
    template_name = 'hrm/payroll_report.html'
    permission_required = PAYROLL_DETAIL

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_section='hrm', hrm_group='salary', hrm_key='payroll-report', page_label='Payroll reports',
            report_api_url=reverse('hrm:payroll-report'), month_default=timezone.localdate().strftime('%Y-%m'),
            currency_default=settings.INVENTORY_BASE_CURRENCY)
        return context


class EmployeeDetailPageView(HRMPageView):
    template_name = 'hrm/employee_detail.html'
    resource_key = 'employee'
    mode = 'detail'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = get_object_or_404(Employee, pk=self.kwargs['pk'])
        context.update({
            'page_label': 'Employee details',
            'detail_api_url': reverse('hrm:employee-detail', args=[employee.pk]),
        })
        if self.request.user.has_perm('hrm.change_employee'):
            context['edit_url'] = reverse('hrm_pages:edit-employee', args=[employee.pk])
        return context


class HRMAddPageView(HRMPageView):
    template_name = 'inventory/add.html'
    mode = 'add'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        resource = self.resource
        employee = resource.key == 'employee'
        heading = f'Add {resource.singular}'
        today = timezone.localdate().isoformat()
        lookup_fields = ('lookup', 'work_schedule', 'leave_type', 'salary_component', 'salary_structure')
        context.update({
            'page_label': heading, 'form_heading': heading, 'save_label': heading,
            'create_api_url': reverse(f'hrm:{resource.key}-list'),
            'fields_template': f'hrm/fields/{resource.fields}.html',
            'form_description': 'Enter employment details and a work contact.' if employee else
                                f'Create a new {resource.singular} for your team.',
            'options_api_url': reverse(f'hrm:{resource.key}-options') if resource.has_options else '',
            'can_link_user': self.request.user.has_perm('auth.change_user'),
            'joining_date_default': today, 'today': today,
            'weekday_names': day_name,
            'attendance_status_default': EmployeeAttendanceStatus.Code.UNRECORDED,
            'attendance_timezone': timezone.get_current_timezone_name(),
            'code_maxlength': resource.model._meta.get_field('code').max_length
                              if resource.fields in lookup_fields else None,
            'name_maxlength': resource.model._meta.get_field('name').max_length
                              if resource.fields in (*lookup_fields, 'holiday') else None,
        })
        if resource.key in ('salary-structure', 'payroll-adjustment', 'employee-loan'):
            context['currency_default'] = settings.INVENTORY_BASE_CURRENCY
        return context


class HRMEditPageView(HRMAddPageView):
    mode = 'edit'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = get_object_or_404(self.resource.model, pk=self.kwargs['pk'])
        heading = f'Edit {self.resource.singular}'
        context.update({
            'page_label': heading, 'form_heading': heading, 'save_label': 'Save changes',
            'form_method': 'PATCH', 'lock_code': self.resource_key != 'employee',
            'create_api_url': reverse(f'hrm:{self.resource.key}-update', args=[target.pk]),
            'detail_api_url': reverse(f'hrm:{self.resource.key}-detail', args=[target.pk]),
            'form_description': f'Update your {self.resource.singular} details.',
        })
        if self.resource.has_options:
            context['options_api_url'] = reverse(f'hrm:{self.resource.key}-edit-options', args=[target.pk])
        if isinstance(target, PayrollAdjustment):
            context['readonly_field'] = 'is_locked'
            context['readonly_message'] = 'Only draft adjustments can be edited. Open the adjustment to review its status.'
        if isinstance(target, Employee):
            if target.user_id and target.user.is_superuser and not self.request.user.is_superuser:
                context['can_link_user'] = False
        return context


class LeaveRequestListPageView(HRMListPageView):
    resource_key = 'leave-request'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.has_perm('hrm.view_leavetype'):
            context['secondary_url'] = reverse('hrm_pages:leave-type-list')
            context['secondary_label'] = 'Manage leave types'
        return context


class LeaveApplyPageView(HRMAddPageView):
    resource_key = 'leave-request'

    def has_permission(self):
        return can_apply_for_leave(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_label': 'Apply for leave', 'form_heading': 'Apply for leave', 'save_label': 'Submit request',
            'form_description': 'Choose a leave type and enter the dates of your request.',
            'can_choose_employee': self.request.user.has_perm(REVIEW_LEAVE_PERMISSION),
        })
        return context


class LeaveRequestDetailPageView(HRMPageView):
    template_name = 'hrm/leave_request_detail.html'
    resource_key = 'leave-request'
    mode = 'detail'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        queryset = LeaveRequest.objects.select_related('employee', 'leave_type', 'reviewed_by')
        leave = get_object_or_404(leave_requests_for_user(queryset, user), pk=self.kwargs['pk'])
        context.update({'page_label': 'Leave request details', 'leave': leave})
        if leave.status == LeaveRequest.Status.PENDING and user.has_perm(REVIEW_LEAVE_PERMISSION) and leave.employee.user_id != user.pk:
            context['review_api_url'] = reverse('hrm:leave-request-review', args=[leave.pk])
        return context
