from django.core.exceptions import PermissionDenied

from account.permissions import check_user_edit_target


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
