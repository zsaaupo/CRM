from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProductTradeStatusMigrationTests(TransactionTestCase):
    before = [('common', '0001_initial'), ('inventory', '0005_alter_product_status_delete_status'),
              ('purchase', '0002_purchaseorder_supplier_party'), ('sale', '0001_initial')]

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_rename_preserves_rows_links_and_grants_and_merges_duplicate_content_types(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.addCleanup(self.restore)
        apps = executor.loader.project_state(self.before).apps
        Status = apps.get_model('common', 'Status')
        status = Status.objects.create(code='migration-custom', name='Custom', description='Preserve',
                                       is_active=False, create_by='creator', modified_by='editor')
        apps.get_model('inventory', 'Currency').objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': '1'})
        apps.get_model('inventory', 'ProductType').objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        unit = apps.get_model('inventory', 'UnitOfMeasure').objects.create(name='Pieces', code='MIGRATION-PCS')
        product = apps.get_model('inventory', 'Product').objects.create(name='Product', sku='MIGRATION-STATUS',
            unit=unit, currency_id='BDT', status_id=status.code)
        purchase = apps.get_model('purchase', 'PurchaseOrder').objects.create(currency_id='BDT', exchange_rate='1', status_id=status.code)
        sale = apps.get_model('sale', 'SalesOrder').objects.create(currency_id='BDT', exchange_rate='1', status_id=status.code)
        ContentType = apps.get_model('contenttypes', 'ContentType')
        Permission = apps.get_model('auth', 'Permission')
        Group = apps.get_model('auth', 'Group')
        User = apps.get_model('auth', 'User')
        old_type, _ = ContentType.objects.get_or_create(app_label='common', model='status')
        original, _ = Permission.objects.get_or_create(content_type_id=old_type.pk, codename='view_status', defaults={'name': 'Can view status'})
        role = Group.objects.create(name='Legacy status readers')
        role.permissions.add(original)
        user = User.objects.create(username='legacy-status-user')
        user.user_permissions.add(original)
        duplicate_type, _ = ContentType.objects.get_or_create(app_label='common', model='productstatus')
        duplicate, _ = Permission.objects.get_or_create(content_type_id=duplicate_type.pk, codename='view_productstatus', defaults={'name': 'Can view product status'})
        duplicate_user = User.objects.create(username='duplicate-status-user')
        duplicate_user.user_permissions.add(duplicate)
        self.restore()
        executor = MigrationExecutor(connection)
        latest = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        ProductStatus = latest.get_model('common', 'ProductStatus')
        TradeStatus = latest.get_model('common', 'TradeStatus')
        migrated = ProductStatus.objects.get(pk=status.pk)
        for field in ['code', 'name', 'description', 'is_active', 'created_at', 'updated_at', 'create_by', 'modified_by']:
            self.assertEqual(getattr(migrated, field), getattr(status, field))
        self.assertEqual(TradeStatus.objects.get(pk=status.pk).code, status.code)
        for app, model, pk in [('inventory', 'Product', product.pk), ('purchase', 'PurchaseOrder', purchase.pk), ('sale', 'SalesOrder', sale.pk)]:
            obj = latest.get_model(app, model).objects.select_related('status').get(pk=pk)
            self.assertEqual(obj.status_id, status.code)
            self.assertEqual(obj.status.pk, status.pk)
        NewPermission = latest.get_model('auth', 'Permission')
        new_type = latest.get_model('contenttypes', 'ContentType').objects.get(app_label='common', model='productstatus')
        self.assertEqual(new_type.pk, old_type.pk)
        self.assertEqual(NewPermission.objects.get(pk=original.pk).codename, 'view_productstatus')
        trade_permission = NewPermission.objects.get(content_type__app_label='common', codename='view_tradestatus')
        for owner, model in [(user, 'User'), (duplicate_user, 'User'), (role, 'Group')]:
            new_owner = latest.get_model('auth', model).objects.get(pk=owner.pk)
            permissions = new_owner.permissions if model == 'Group' else new_owner.user_permissions
            self.assertTrue(permissions.filter(pk=original.pk).exists())
            self.assertTrue(permissions.filter(pk=trade_permission.pk).exists())
        executor.migrate(self.before)
        restored = executor.loader.project_state(self.before).apps
        old_status = restored.get_model('common', 'Status').objects.get(pk=status.pk)
        self.assertEqual(old_status.updated_at, status.updated_at)
        self.assertEqual(restored.get_model('auth', 'Permission').objects.get(pk=original.pk).codename, 'view_status')
        self.assertEqual(restored.get_model('purchase', 'PurchaseOrder').objects.get(pk=purchase.pk).status_id, status.code)
        self.assertEqual(restored.get_model('sale', 'SalesOrder').objects.get(pk=sale.pk).status_id, status.code)
