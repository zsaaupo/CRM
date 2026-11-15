from django.contrib.auth.models import Group

from warehouse.models import RoleWarehouseAccess


def assign_warehouse_role(user, *, warehouse=None, all_warehouses=False, role=None):
    role = role or Group.objects.create(name=f'{user.get_username()} warehouse role {user.pk}')
    user.groups.set([role])
    access, _ = RoleWarehouseAccess.objects.update_or_create(role=role, defaults={
        'warehouse': warehouse, 'all_warehouses': all_warehouses,
    })
    return access
