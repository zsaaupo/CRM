from rest_framework.permissions import BasePermission
from django.core.exceptions import PermissionDenied


ACCESS_RULES = {
    'view_users': ('auth.view_user',),
    'add_user': ('auth.add_user',),
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


def check_role_permission_target(actor, group):
    if not actor.is_superuser and actor.groups.filter(pk=group.pk).exists():
        raise PermissionDenied('Only a superuser can change permissions of their own role.')


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
            and request.user.has_perms(permissions)
        )
