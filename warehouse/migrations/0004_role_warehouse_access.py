import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def copy_permissions(apps, alias, source_model, target_model):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model(settings.AUTH_USER_MODEL)
    source = ContentType.objects.using(alias).filter(app_label='warehouse', model=source_model).first()
    target, _ = ContentType.objects.using(alias).get_or_create(app_label='warehouse', model=target_model)
    group_links = Group._meta.get_field('permissions').remote_field.through
    user_links = User._meta.get_field('user_permissions').remote_field.through
    for action in ('view', 'add', 'change', 'delete'):
        old = Permission.objects.using(alias).filter(content_type=source, codename=f'{action}_{source_model}').first()
        new, _ = Permission.objects.using(alias).get_or_create(content_type=target, codename=f'{action}_{target_model}',
            defaults={'name': f'Can {action} {"role" if target_model.startswith("role") else "user"} warehouse access'})
        if old:
            for group_id in group_links.objects.using(alias).filter(permission_id=old.pk).values_list('group_id', flat=True):
                group_links.objects.using(alias).get_or_create(group_id=group_id, permission_id=new.pk)
            for user_id in user_links.objects.using(alias).filter(permission_id=old.pk).values_list('user_id', flat=True):
                user_links.objects.using(alias).get_or_create(user_id=user_id, permission_id=new.pk)
    if source:
        source.delete(using=alias)


def to_roles(apps, schema_editor):
    alias = schema_editor.connection.alias
    Old = apps.get_model('warehouse', 'UserWarehouseAccess')
    New = apps.get_model('warehouse', 'RoleWarehouseAccess')
    User = apps.get_model(settings.AUTH_USER_MODEL)
    rows = list(Old.objects.using(alias).select_related('user').order_by('pk'))
    by_user = {row.user_id: row for row in rows}
    plans = {}
    conflicts = set()
    for row in rows:
        if row.user.is_superuser:
            continue  # Superusers remain unrestricted in both versions.
        roles = list(row.user.groups.using(alias).values_list('pk', flat=True))
        if len(roles) != 1:
            if row.warehouse_id is not None or row.all_warehouses:
                conflicts.add(f'user {row.user_id} needs exactly one role')
            continue
        role_id = roles[0]
        values = set()
        for member_id in User.objects.using(alias).filter(groups__pk=role_id, is_superuser=False).values_list('pk', flat=True):
            existing = by_user.get(member_id)
            values.add((existing.warehouse_id, existing.all_warehouses) if existing else (None, False))
        if len(values) != 1:
            conflicts.add(f'role {role_id} has members with different warehouse access')
        else:
            plans.setdefault(role_id, row)
    if conflicts:
        raise RuntimeError('Cannot safely convert user warehouse access to roles: ' + '; '.join(sorted(conflicts)))
    for role_id, row in plans.items():
        new = New.objects.using(alias).create(role_id=role_id, warehouse_id=row.warehouse_id,
            all_warehouses=row.all_warehouses, create_by=row.create_by, modified_by=row.modified_by)
        New.objects.using(alias).filter(pk=new.pk).update(created_at=row.created_at, updated_at=row.updated_at)
    copy_permissions(apps, alias, 'userwarehouseaccess', 'rolewarehouseaccess')


def to_users(apps, schema_editor):
    alias = schema_editor.connection.alias
    Old = apps.get_model('warehouse', 'UserWarehouseAccess')
    New = apps.get_model('warehouse', 'RoleWarehouseAccess')
    User = apps.get_model(settings.AUTH_USER_MODEL)
    for row in New.objects.using(alias).all():
        for user in User.objects.using(alias).filter(groups__pk=row.role_id):
            if user.groups.using(alias).count() != 1:
                continue
            old = Old.objects.using(alias).create(user_id=user.pk, warehouse_id=row.warehouse_id,
                all_warehouses=row.all_warehouses, create_by=row.create_by, modified_by=row.modified_by)
            Old.objects.using(alias).filter(pk=old.pk).update(created_at=row.created_at, updated_at=row.updated_at)
    copy_permissions(apps, alias, 'rolewarehouseaccess', 'userwarehouseaccess')


class Migration(migrations.Migration):
    dependencies = [
        ('warehouse', '0003_user_warehouse_access'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='RoleWarehouseAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('create_by', models.CharField(max_length=255, null=True)),
                ('modified_by', models.CharField(max_length=255, null=True)),
                ('all_warehouses', models.BooleanField(default=False,
                    help_text="Allow access to all warehouses, subject to the role's CRUD permissions.")),
                ('role', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='warehouse_access', to='auth.group')),
                ('warehouse', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='role_access_assignments', to='warehouse.warehouse')),
            ],
            options={
                'ordering': ['role_id'],
                'verbose_name': 'role warehouse access',
                'verbose_name_plural': 'role warehouse access assignments',
                'constraints': [models.CheckConstraint(condition=models.Q(all_warehouses=False) | models.Q(warehouse__isnull=True),
                    name='warehouse_role_access_scope_valid')],
            },
        ),
        migrations.RunPython(to_roles, to_users),
        migrations.DeleteModel(name='UserWarehouseAccess'),
    ]
