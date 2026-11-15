"""Warehouse data scope, used alongside existing Django CRUD permissions."""
from django.db import OperationalError, transaction
from django.db.models import F
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import BasePermission

from .access import WarehouseAccessBusy
from .models import RoleWarehouseAccess, WareHouse
from account.permissions import has_role_permissions, lock_user_access


def warehouse_scope(user):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied('An active authenticated user is required.')
    if user.is_superuser:
        return None
    role_ids = list(user.groups.order_by('pk').values_list('pk', flat=True)[:2])
    if len(role_ids) != 1:
        raise PermissionDenied('A single role with warehouse access must be assigned to your user.')
    scope = RoleWarehouseAccess.objects.filter(role_id=role_ids[0]).first()
    if scope is None or (not scope.all_warehouses and scope.warehouse_id is None):
        raise PermissionDenied('Your role has no warehouse access assigned. Contact an administrator.')
    return scope


def has_warehouse_scope(user):
    try:
        warehouse_scope(user)
        return True
    except PermissionDenied:
        return False


def all_warehouse_access(scope):
    return scope is None or scope.all_warehouses


def scoped_queryset(queryset, scope, lookup='warehouse_id'):
    return queryset if all_warehouse_access(scope) else queryset.filter(**{lookup: scope.warehouse_id})


def check_warehouse(scope, warehouse_id, *, hide=False):
    if not all_warehouse_access(scope) and warehouse_id != scope.warehouse_id:
        if hide:
            raise NotFound('Record not found.')
        raise PermissionDenied('You can only access your assigned warehouse.')


def lock_warehouse_scope(user):
    """Call inside atomic, before reading business records, to serialize reassignment."""
    if not user.is_superuser:
        lock_user_access([user.pk])
        role_ids = user.groups.values_list('pk', flat=True)
        RoleWarehouseAccess.objects.filter(role_id__in=role_ids).update(all_warehouses=F('all_warehouses'))
    return warehouse_scope(user)


def warehouse_scope_options(user):
    scope = warehouse_scope(user)
    return {'all_warehouses': all_warehouse_access(scope),
            'warehouse': None if all_warehouse_access(scope) else scope.warehouse_id}


class HasWarehouseAccess(BasePermission):
    def has_permission(self, request, view):
        if request.method.lower() in view.http_method_names:
            request.warehouse_scope = warehouse_scope(request.user)
        return True


class WarehouseScopeAPIMixin:
    role_permissions_only = True
    warehouse_lookup = 'warehouse_id'

    def get_permissions(self):
        return [*super().get_permissions(), HasWarehouseAccess()]

    def get_queryset(self):
        return scoped_queryset(super().get_queryset(), self.request.warehouse_scope, self.warehouse_lookup)

    def save_record(self, serializer, **audit):
        with transaction.atomic():
            scope = lock_warehouse_scope(self.request.user)
            self.check_permissions(self.request)
            if self.warehouse_lookup == 'pk':
                if serializer.instance is None:
                    if not all_warehouse_access(scope):
                        raise PermissionDenied('Creating warehouses requires all-warehouse access.')
                else:
                    serializer.instance = scoped_queryset(
                        WareHouse.objects.select_for_update(), scope, 'pk',
                    ).filter(pk=serializer.instance.pk).first()
                    if serializer.instance is None:
                        raise NotFound('Warehouse not found.')
            serializer.validated_data.update(serializer.validate(dict(serializer.validated_data)))
            return super().save_record(serializer, **audit)

    @transaction.atomic
    def perform_destroy(self, instance):
        scope = lock_warehouse_scope(self.request.user)
        self.check_permissions(self.request)
        if self.warehouse_lookup == 'pk':
            check_warehouse(scope, instance.pk, hide=True)
        return super().perform_destroy(instance)

    def handle_exception(self, exc):
        if isinstance(exc, OperationalError) and ('locked' in str(exc).lower() or 'deadlock' in str(exc).lower()):
            exc = WarehouseAccessBusy()
        return super().handle_exception(exc)


class WarehouseScopedSerializerMixin:
    warehouse_parent_field = None
    warehouse_is_record = False

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if request and self.warehouse_parent_field:
            field = fields[self.warehouse_parent_field]
            field.queryset = scoped_queryset(field.queryset, warehouse_scope(request.user))
        return fields

    def validate(self, attrs):
        request = self.context.get('request')
        if request:
            scope = warehouse_scope(request.user)
            if self.warehouse_is_record:
                if self.instance:
                    check_warehouse(scope, self.instance.pk, hide=True)
                elif not all_warehouse_access(scope):
                    raise PermissionDenied('Creating warehouses requires all-warehouse access.')
            elif self.warehouse_parent_field:
                parent = attrs.get(self.warehouse_parent_field)
                if parent is None and self.instance:
                    parent = getattr(self.instance, self.warehouse_parent_field)
                if parent:
                    check_warehouse(scope, parent.warehouse_id)
            else:
                if self.instance is None and 'warehouse' not in attrs and not all_warehouse_access(scope):
                    attrs['warehouse'] = scope.warehouse
                warehouse = attrs.get('warehouse', self.instance.warehouse if self.instance else None)
                check_warehouse(scope, warehouse.pk if warehouse else None)
        return super().validate(attrs)


class WarehouseScopePageMixin:
    requires_all_warehouses = False

    def has_permission(self):
        if not super().has_permission():
            return False
        if not has_role_permissions(self.request.user, self.get_permission_required()):
            return False
        try:
            scope = warehouse_scope(self.request.user)
            return not self.requires_all_warehouses or all_warehouse_access(scope)
        except PermissionDenied:
            return False
