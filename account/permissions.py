from rest_framework.permissions import BasePermission
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import F


ACCESS_RULES = {
    'view_users': ('auth.view_user',),
    'add_user': ('auth.add_user',),
    'change_user': ('auth.change_user',),
    'view_roles': ('auth.view_group',),
    'add_role': ('auth.add_group',),
    'change_role': ('auth.change_group',),
    'delete_role': ('auth.delete_group', 'account.manage_access'),
    'view_permissions': ('auth.view_permission',),
    'assign_role': ('auth.view_user', 'auth.change_user', 'auth.view_group', 'account.manage_access'),
    'manage_role_permissions': (
        'auth.view_group', 'auth.change_group', 'auth.view_permission', 'account.manage_access',
    ),
}


def access_flags(user):
    return {name: user.has_perms(permissions) for name, permissions in ACCESS_RULES.items()}


def check_assignment_target(actor, target):
    if not actor.is_superuser and (actor.pk == target.pk or target.is_superuser):
        raise PermissionDenied('Only a superuser can change their own role or a superuser role.')


def check_user_edit_target(actor, target):
    if target.is_superuser and not actor.is_superuser:
        raise PermissionDenied('Only a superuser can edit a superuser account.')


def check_role_permission_target(actor, group):
    if not actor.is_superuser and actor.groups.filter(pk=group.pk).exists():
        raise PermissionDenied('Only a superuser can change permissions of their own role.')


def lock_user_access(user_ids):
    """Call inside atomic before reading/changing a user's role-dependent access."""
    get_user_model().objects.filter(pk__in=sorted(set(user_ids))).update(is_active=F('is_active'))


def has_role_permissions(user, permissions):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    roles = list(user.groups.order_by('pk').values_list('pk', flat=True)[:2])
    if len(roles) != 1:
        return False
    granted = {f'{app}.{code}' for app, code in Permission.objects.filter(group__pk=roles[0]).values_list(
        'content_type__app_label', 'codename')}
    return all(permission in granted for permission in permissions)


class HasRequiredPermissions(BasePermission):
    """Require the view's declared Django permissions, including group grants."""

    message = 'You do not have permission to access this resource.'

    def has_permission(self, request, view):
        if request.method.lower() not in view.http_method_names:
            return request.user.is_authenticated
        permissions = getattr(view, 'required_permissions', ())
        if isinstance(permissions, dict):
            method = 'GET' if request.method in ('HEAD', 'OPTIONS') else request.method
            permissions = permissions.get(method, ())
        if isinstance(permissions, str):
            permissions = (permissions,)
        return bool(
            request.user.is_authenticated
            and permissions
            and (has_role_permissions(request.user, permissions) if getattr(view, 'role_permissions_only', False)
                 else request.user.has_perms(permissions))
        )
