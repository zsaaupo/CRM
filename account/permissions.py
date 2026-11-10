from rest_framework.permissions import BasePermission


class HasRequiredPermissions(BasePermission):
    """Require the view's declared Django permissions, including group grants."""

    message = 'You do not have permission to access this resource.'

    def has_permission(self, request, view):
        permissions = getattr(view, 'required_permissions', ())
        if isinstance(permissions, str):
            permissions = (permissions,)
        return bool(
            request.user.is_authenticated
            and permissions
            and request.user.has_perms(permissions)
        )
