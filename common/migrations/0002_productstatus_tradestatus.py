from django.conf import settings
from django.db import migrations


def copy_grants(apps, database, source, target):
    if source.pk == target.pk:
        return
    owners = [(apps.get_model('auth', 'Group'), 'permissions'),
              (apps.get_model(*settings.AUTH_USER_MODEL.split('.')), 'user_permissions')]
    for owner, field_name in owners:
        field = owner._meta.get_field(field_name)
        through = field.remote_field.through
        owner_key = f'{field.m2m_field_name()}_id'
        ids = through.objects.using(database).filter(permission_id=source.pk).values_list(owner_key, flat=True)
        through.objects.using(database).bulk_create(
            [through(**{owner_key: pk, 'permission_id': target.pk}) for pk in ids], ignore_conflicts=True)


def rename_content_type(apps, database, old_model, new_model):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    old = ContentType.objects.using(database).filter(app_label='common', model=old_model).first()
    new = ContentType.objects.using(database).filter(app_label='common', model=new_model).first()
    if old is None:
        return new or ContentType.objects.using(database).create(app_label='common', model=new_model)
    if new is not None and new.pk != old.pk:
        for permission in Permission.objects.using(database).filter(content_type_id=new.pk):
            target, _ = Permission.objects.using(database).get_or_create(
                content_type_id=old.pk, codename=permission.codename, defaults={'name': permission.name})
            copy_grants(apps, database, permission, target)
        try:
            LogEntry = apps.get_model('admin', 'LogEntry')
        except LookupError:
            pass
        else:
            LogEntry.objects.using(database).filter(content_type_id=new.pk).update(content_type_id=old.pk)
        new.delete(using=database)
    old.model = new_model
    old.save(using=database, update_fields=['model'])
    return old


def rename_permissions(apps, database, content_type, old_model, new_model, label):
    Permission = apps.get_model('auth', 'Permission')
    renamed = {}
    for action in ('add', 'change', 'delete', 'view'):
        old = Permission.objects.using(database).filter(content_type_id=content_type.pk,
                                                        codename=f'{action}_{old_model}').first()
        target = Permission.objects.using(database).filter(content_type_id=content_type.pk,
                                                           codename=f'{action}_{new_model}').first()
        if old is not None:
            if target is not None and target.pk != old.pk:
                copy_grants(apps, database, target, old)
                target.delete(using=database)
            old.codename = f'{action}_{new_model}'
            old.name = f'Can {action} {label}'
            old.save(using=database, update_fields=['codename', 'name'])
            target = old
        elif target is None:
            target = Permission.objects.using(database).create(
                content_type_id=content_type.pk, codename=f'{action}_{new_model}', name=f'Can {action} {label}')
        renamed[action] = target
    return renamed


def migrate_permissions(apps, schema_editor):
    database = schema_editor.connection.alias
    product_type = rename_content_type(apps, database, 'status', 'productstatus')
    product_permissions = rename_permissions(apps, database, product_type, 'status', 'productstatus', 'product status')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    trade_type, _ = ContentType.objects.using(database).get_or_create(app_label='common', model='tradestatus')
    for action, source in product_permissions.items():
        target, _ = Permission.objects.using(database).get_or_create(
            content_type_id=trade_type.pk, codename=f'{action}_tradestatus',
            defaults={'name': f'Can {action} trade status'})
        copy_grants(apps, database, source, target)


def restore_permissions(apps, schema_editor):
    database = schema_editor.connection.alias
    content_type = rename_content_type(apps, database, 'productstatus', 'status')
    permissions = rename_permissions(apps, database, content_type, 'productstatus', 'status', 'status')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    trade = ContentType.objects.using(database).filter(app_label='common', model='tradestatus').first()
    if trade is not None:
        for action, target in permissions.items():
            source = Permission.objects.using(database).filter(content_type_id=trade.pk, codename=f'{action}_tradestatus').first()
            if source is not None:
                copy_grants(apps, database, source, target)
        try:
            LogEntry = apps.get_model('admin', 'LogEntry')
        except LookupError:
            pass
        else:
            LogEntry.objects.using(database).filter(content_type_id=trade.pk).update(content_type_id=content_type.pk)
        trade.delete(using=database)


class Migration(migrations.Migration):
    dependencies = [
        ('common', '0001_initial'),
        ('inventory', '0005_alter_product_status_delete_status'),
        ('purchase', '0002_purchaseorder_supplier_party'),
        ('sale', '0001_initial'),
    ]

    operations = [
        migrations.RenameModel(old_name='Status', new_name='ProductStatus'),
        migrations.AlterModelOptions(
            name='productstatus', options={'ordering': ['name', 'pk'], 'verbose_name': 'product status',
                                           'verbose_name_plural': 'product statuses'}),
        migrations.CreateModel(
            name='TradeStatus', fields=[],
            options={'proxy': True, 'verbose_name': 'trade status', 'verbose_name_plural': 'trade statuses',
                     'indexes': [], 'constraints': []},
            bases=('common.productstatus',),
        ),
        migrations.RunPython(migrate_permissions, restore_permissions),
    ]
