from django.core.exceptions import PermissionDenied

from account.permissions import check_user_edit_target

from .models import Employee


REVIEW_LEAVE_PERMISSION = 'hrm.review_leaverequest'
VIEW_LEAVE_PERMISSION = 'hrm.view_leaverequest'
ADD_LEAVE_PERMISSION = 'hrm.add_leaverequest'


def leave_requests_for_user(queryset, user):
    if user.has_perm(REVIEW_LEAVE_PERMISSION):
        return queryset
    return queryset.filter(employee__user_id=user.pk, employee__is_archived=False)


def can_apply_for_leave(user):
    return user.has_perms((VIEW_LEAVE_PERMISSION, ADD_LEAVE_PERMISSION)) and (
        user.has_perm(REVIEW_LEAVE_PERMISSION) or Employee.objects.filter(user_id=user.pk).exists()
    )


def check_employee_user_link(actor, instance, user):
    """Keep login linking separate from ordinary employee record permissions."""
    previous_id = instance.user_id if instance else None
    if previous_id == (user.pk if user else None):
        return
    if actor is None or not actor.has_perm('auth.change_user'):
        raise PermissionDenied('Changing an employee login link requires permission to change users.')
    for target in [instance.user if instance and previous_id else None, user]:
        if target is not None:
            check_user_edit_target(actor, target)
