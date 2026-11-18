from django.urls import reverse

from .catalog import ATTENDANCE_RESOURCES, EMPLOYEE_RESOURCES, LEAVE_TYPE_RESOURCE
from .permissions import VIEW_LEAVE_PERMISSION, can_apply_for_leave


def hrm_navigation(request):
    if not request.user.is_authenticated:
        return {'hrm_menu': [], 'hrm_attendance_menu': [], 'hrm_leave_menu': []}

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
    return {'hrm_menu': menu(EMPLOYEE_RESOURCES), 'hrm_attendance_menu': menu(ATTENDANCE_RESOURCES),
            'hrm_leave_menu': leave}
