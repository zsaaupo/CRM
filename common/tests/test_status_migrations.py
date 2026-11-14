from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SharedStatusMigrationTests(TransactionTestCase):
    before = [('inventory', '0004_currency_product_currency'),
              ('auth', '0012_alter_user_first_name_max_length'),
              ('contenttypes', '0002_remove_content_type_name'), ('common', None)]

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_status_data_product_links_and_permission_grants_survive_move_and_rollback(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.addCleanup(self.restore)
        apps = executor.loader.project_state([node for node in self.before if node[1] is not None]).apps
        OldStatus = apps.get_model('inventory', 'Status')
        Product = apps.get_model('inventory', 'Product')
        Currency = apps.get_model('inventory', 'Currency')
        apps.get_model('inventory', 'ProductType').objects.get_or_create(code='goods', defaults={'name': 'Physical goods'})
        Unit = apps.get_model('inventory', 'UnitOfMeasure')
        ContentType = apps.get_model('contenttypes', 'ContentType')
        Permission = apps.get_model('auth', 'Permission')
        Group = apps.get_model('auth', 'Group')
        User = apps.get_model('auth', 'User')

        status = OldStatus.objects.create(code='custom-ready', name='Custom ready', description='Preserve details',
                                          is_active=False, create_by='original', modified_by='editor')
        unit = Unit.objects.create(name='Pieces', code='STATUS-PCS')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': '1'})
        product = Product.objects.create(name='Legacy product', sku='STATUS-001', currency_id='BDT',
                                          unit=unit, status=status)
        ContentType.objects.filter(app_label='common', model='status').delete()
        content_type, _ = ContentType.objects.get_or_create(app_label='inventory', model='status')
        permission, _ = Permission.objects.get_or_create(content_type=content_type, codename='view_status',
                                                         defaults={'name': 'Can view status'})
        role = Group.objects.create(name='Status readers')
        role.permissions.add(permission)
        user = User.objects.create(username='status-migration-user')
        user.user_permissions.add(permission)

        self.restore()
        executor = MigrationExecutor(connection)
        apps = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        Status = apps.get_model('common', 'Status')
        NewProduct = apps.get_model('inventory', 'Product')
        new_status = Status.objects.get(pk=status.pk)
        for field in ['code', 'name', 'description', 'is_active', 'create_by', 'modified_by', 'created_at', 'updated_at']:
            self.assertEqual(getattr(new_status, field), getattr(status, field))
        self.assertEqual(NewProduct.objects.select_related('status').get(pk=product.pk).status.pk, status.pk)
        moved = apps.get_model('contenttypes', 'ContentType').objects.get(app_label='common', model='status')
        self.assertEqual(moved.pk, content_type.pk)
        self.assertTrue(apps.get_model('auth', 'Group').objects.get(pk=role.pk).permissions.filter(pk=permission.pk).exists())
        self.assertTrue(apps.get_model('auth', 'User').objects.get(pk=user.pk).user_permissions.filter(pk=permission.pk).exists())
        self.assertEqual(Status.objects.filter(code__in=['draft', 'confirmed', 'closed', 'cancelled']).count(), 4)

        executor.migrate(self.before)
        old_apps = executor.loader.project_state([node for node in self.before if node[1] is not None]).apps
        restored = old_apps.get_model('inventory', 'Status').objects.get(pk=status.pk)
        self.assertEqual(restored.code, 'custom-ready')
        self.assertEqual(restored.updated_at, status.updated_at)
        self.assertEqual(old_apps.get_model('inventory', 'Product').objects.get(pk=product.pk).status_id, 'custom-ready')
