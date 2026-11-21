from django.urls import reverse

from .catalog import (ATTENDANCE_RESOURCES, EMPLOYEE_RESOURCES, LEAVE_TYPE_RESOURCE, LOAN_RESOURCES, PAYROLL_INPUT_RESOURCES,
                      PAYROLL_RUN_RESOURCE, SALARY_RESOURCES)
from .permissions import PAYROLL_DETAIL, VIEW_LEAVE_PERMISSION, can_apply_for_leave


def hrm_navigation(request):
    if not request.user.is_authenticated:
        return {'hrm_menu': [], 'hrm_attendance_menu': [], 'hrm_leave_menu': [], 'hrm_salary_menu': [], 'hrm_loan_menu': []}

    def menu(resources):
        return [
            {
                'key': resource.key,
                'label': f'{resource.singular.capitalize()} list',
                'list_url': reverse(f'hrm_pages:{resource.key}-list'),
                'add_url': reverse(f'hrm_pages:add-{resource.key}'),
                'can_add': request.user.has_perm(resource.permission('add')),
            }
            for resource in resources if request.user.has_perm(resource.permission('view'))
        ]

    leave = []
    if request.user.has_perm(VIEW_LEAVE_PERMISSION):
        leave.append({
            'key': 'leave-request', 'label': 'Request list',
            'list_url': reverse('hrm_pages:leave-request-list'),
            'add_url': reverse('hrm_pages:add-leave-request'),
            'can_add': can_apply_for_leave(request.user),
        })
    leave.extend(menu((LEAVE_TYPE_RESOURCE,)))
    salary = menu((*SALARY_RESOURCES, PAYROLL_RUN_RESOURCE, *PAYROLL_INPUT_RESOURCES))
    if request.user.has_perms(PAYROLL_DETAIL):
        salary.append({'key': 'payroll-report', 'label': 'Payroll reports', 'list_url': reverse('hrm_pages:payroll-report')})
    return {'hrm_menu': menu(EMPLOYEE_RESOURCES), 'hrm_attendance_menu': menu(ATTENDANCE_RESOURCES),
            'hrm_leave_menu': leave, 'hrm_salary_menu': salary, 'hrm_loan_menu': menu(LOAN_RESOURCES)}
