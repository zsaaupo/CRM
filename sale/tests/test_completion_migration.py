from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SaleCompletionMigrationTests(TransactionTestCase):
    before = [('sale', '0002_alter_salesorder_status'), ('common', '0003_independent_trade_status')]
    after = [('sale', '0003_salesorder_is_completed')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_sales_preserved_with_independent_trade_status_fk(self):
        self.addCleanup(self.restore)
        old = self.migrate(self.before)
        old.get_model('inventory', 'Currency').objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        status = old.get_model('common', 'TradeStatus').objects.create(code='sale-migration', name='Trade only')
        order = old.get_model('sale', 'SalesOrder').objects.create(currency_id='BDT', exchange_rate=1,
            status_id=status.code, notes='Preserve', create_by='original', modified_by='editor')
        new = self.migrate(self.after)
        Order = new.get_model('sale', 'SalesOrder')
        migrated = Order.objects.select_related('status').get(pk=order.pk)
        self.assertFalse(migrated.is_completed)
        for field in ['order_number', 'status_id', 'notes', 'create_by', 'modified_by', 'created_at', 'updated_at']:
            self.assertEqual(getattr(migrated, field), getattr(order, field))
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, Order._meta.db_table)
        targets = [c['foreign_key'] for c in constraints.values() if c['columns'] == ['status_id'] and c.get('foreign_key')]
        self.assertEqual(targets, [('common_tradestatus', 'code')])
        connection.check_constraints()
        restored = self.migrate(self.before)
        self.assertEqual(restored.get_model('sale', 'SalesOrder').objects.select_related('status').get(pk=order.pk).status.code, status.code)
        connection.check_constraints()
