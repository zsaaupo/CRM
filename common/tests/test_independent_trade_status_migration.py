from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class IndependentTradeStatusMigrationTests(TransactionTestCase):
    before = [('common', '0002_productstatus_tradestatus'),
              ('purchase', '0003_alter_purchaseorder_status'),
              ('sale', '0002_alter_salesorder_status')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_separation_preserves_data_grants_links_and_physical_foreign_keys_and_reverses(self):
        old = self.migrate(self.before)
        self.addCleanup(self.restore)
        status = old.get_model('common', 'ProductStatus').objects.create(
            code='split-test', name='Original', description='Preserve details', is_active=False,
            create_by='creator', modified_by='editor')
        old.get_model('inventory', 'Currency').objects.get_or_create(
            code='BDT', defaults={'name': 'Taka', 'exchange_rate': '1'})
        orders = []
        for app, model in [('purchase', 'PurchaseOrder'), ('sale', 'SalesOrder')]:
            order = old.get_model(app, model).objects.create(
                currency_id='BDT', exchange_rate='1', status_id=status.code)
            orders.append((app, model, order.pk))
        content_type, _ = old.get_model('contenttypes', 'ContentType').objects.get_or_create(
            app_label='common', model='tradestatus')
        permission, _ = old.get_model('auth', 'Permission').objects.get_or_create(
            content_type=content_type, codename='view_tradestatus', defaults={'name': 'Can view trade status'})
        user = old.get_model('auth', 'User').objects.create(username='trade-migration')
        user.user_permissions.add(permission)
        group = old.get_model('auth', 'Group').objects.create(name='Trade migration')
        group.permissions.add(permission)

        latest = self.migrate([('common', '0003_independent_trade_status')])
        ProductStatus = latest.get_model('common', 'ProductStatus')
        TradeStatus = latest.get_model('common', 'TradeStatus')
        trade = TradeStatus.objects.get(pk=status.pk)
        for field in ['code', 'name', 'description', 'is_active', 'created_at', 'updated_at', 'create_by', 'modified_by']:
            self.assertEqual(getattr(trade, field), getattr(status, field))
        self.assertEqual(latest.get_model('auth', 'Permission').objects.get(pk=permission.pk).content_type_id, content_type.pk)
        self.assertTrue(latest.get_model('auth', 'User').objects.get(pk=user.pk).user_permissions.filter(pk=permission.pk).exists())
        self.assertTrue(latest.get_model('auth', 'Group').objects.get(pk=group.pk).permissions.filter(pk=permission.pk).exists())
        trade.name = 'Independent trade label'
        trade.save()
        self.assertEqual(ProductStatus.objects.get(pk=status.pk).name, 'Original')
        new = TradeStatus.objects.create(code='trade-only', name='Trade only')
        for app, model, pk in orders:
            Order = latest.get_model(app, model)
            self.assertEqual(Order.objects.select_related('status').get(pk=pk).status.pk, trade.pk)
            Order.objects.filter(pk=pk).update(status_id=new.code)
            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(cursor, Order._meta.db_table)
            foreign_keys = [c['foreign_key'] for c in constraints.values() if c['columns'] == ['status_id'] and c.get('foreign_key')]
            self.assertEqual(foreign_keys, [(TradeStatus._meta.db_table, 'code')])
        connection.check_constraints()

        restored = self.migrate(self.before)
        self.assertTrue(restored.get_model('common', 'TradeStatus')._meta.proxy)
        self.assertEqual(restored.get_model('common', 'ProductStatus').objects.get(code=status.code).name, 'Original')
        self.assertTrue(restored.get_model('common', 'ProductStatus').objects.filter(code=new.code).exists())
        self.assertNotIn('common_tradestatus', connection.introspection.table_names())
        for app, model, pk in orders:
            self.assertEqual(restored.get_model(app, model).objects.select_related('status').get(pk=pk).status.code, new.code)
        connection.check_constraints()
