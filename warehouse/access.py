"""Authorization for managing warehouse assignments; business data filtering is separate."""
from django.contrib.auth.models import Group
from django.db.models import F, Q
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from .models import RoleWarehouseAccess
from account.permissions import has_role_permissions, lock_user_access


class WarehouseAccessBusy(APIException):
    status_code = 409
    default_detail = 'Warehouse assignments are being updated. Please retry the request.'
    default_code = 'warehouse_access_busy'


def assignment_manager_scope(actor):
    from .scope import warehouse_scope
    return warehouse_scope(actor)


def managed_assignments(queryset, actor):
    scope = assignment_manager_scope(actor)
    if scope is None:
        return queryset
    queryset = queryset.exclude(role__user__is_superuser=True)
    if scope.all_warehouses:
        return queryset
    return queryset.filter(
        Q(warehouse_id=scope.warehouse_id, all_warehouses=False)
        | Q(warehouse__isnull=True, all_warehouses=False),
    )


def lock_assignment_write(actor, role_id, assignment_id=None):
    """Call inside atomic. Serialize scope changes before checking fresh assignments."""
    # A first write also serializes SQLite requests, even when a row is absent.
    lock_user_access([actor.pk])
    actor_roles = list(actor.groups.values_list('pk', flat=True))
    RoleWarehouseAccess.objects.filter(role_id__in=sorted({role_id, *actor_roles})).update(
        all_warehouses=F('all_warehouses'))
    scope = assignment_manager_scope(actor)
    if not has_role_permissions(actor, ('warehouse.manage_warehouse_access',)):
        raise PermissionDenied('Your role does not have permission to manage warehouse assignments.')
    try:
        target = Group.objects.select_for_update().get(pk=role_id)
    except Group.DoesNotExist as exc:
        raise NotFound('Role not found.') from exc
    if not actor.is_superuser and (role_id in actor_roles or target.user_set.filter(is_superuser=True).exists()):
        raise PermissionDenied('Only a superuser can change warehouse access for their own role or a superuser role.')
    current = None
    if assignment_id is not None:
        current = RoleWarehouseAccess.objects.select_for_update().filter(pk=assignment_id).first()
        if current is None:
            raise NotFound('Warehouse assignment not found.')
        if current.role_id != role_id:
            raise PermissionDenied('The assigned role cannot be changed.')
        check_assignment_scope(scope, current.warehouse_id, current.all_warehouses)
    return scope, current


def check_assignment_scope(scope, warehouse_id, all_warehouses):
    if scope is not None and not scope.all_warehouses:
        if all_warehouses or (warehouse_id is not None and warehouse_id != scope.warehouse_id):
            raise PermissionDenied('You can only manage assignments within your own warehouse scope.')


def check_user_role_assignment(actor, target, role):
    """A role change also grants/revokes its warehouse scope."""
    if actor.is_superuser:
        return
    role_ids = set(target.groups.values_list('pk', flat=True)) | {role.pk}
    role_ids.update(actor.groups.values_list('pk', flat=True))
    RoleWarehouseAccess.objects.filter(role_id__in=sorted(role_ids)).update(all_warehouses=F('all_warehouses'))
    assignments = list(RoleWarehouseAccess.objects.filter(role_id__in=role_ids))
    effective = [row for row in assignments if row.warehouse_id is not None or row.all_warehouses]
    if not effective:
        return
    scope = assignment_manager_scope(actor)
    for row in effective:
        check_assignment_scope(scope, row.warehouse_id, row.all_warehouses)
