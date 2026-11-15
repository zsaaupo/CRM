from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PurchaseReceiveMigrationTests(TransactionTestCase):
    before = [('purchase', '0003_alter_purchaseorder_status'),
              ('common', '0003_independent_trade_status'), ('warehouse', '0002_stock')]
    after = [('purchase', '0004_purchaseorder_is_received_purchaseorder_warehouse')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_orders_start_unreceived_and_keep_independent_trade_status_links(self):
        self.addCleanup(self.restore)
        old = self.migrate(self.before)
        old.get_model('inventory', 'Currency').objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        trade = old.get_model('common', 'TradeStatus').objects.create(code='receipt-migration', name='Trade only')
        order = old.get_model('purchase', 'PurchaseOrder').objects.create(currency_id='BDT', exchange_rate=1,
            status_id=trade.code, notes='Preserve', create_by='original', modified_by='editor')
        new = self.migrate(self.after)
        Order = new.get_model('purchase', 'PurchaseOrder')
        migrated = Order.objects.select_related('status').get(pk=order.pk)
        self.assertFalse(migrated.is_received)
        self.assertIsNone(migrated.warehouse_id)
        for field in ['order_number', 'status_id', 'notes', 'create_by', 'modified_by', 'created_at', 'updated_at']:
            self.assertEqual(getattr(migrated, field), getattr(order, field))
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, Order._meta.db_table)
        targets = [c['foreign_key'] for c in constraints.values() if c['columns'] == ['status_id'] and c.get('foreign_key')]
        self.assertEqual(targets, [('common_tradestatus', 'code')])
        connection.check_constraints()
        restored = self.migrate(self.before)
        self.assertEqual(restored.get_model('purchase', 'PurchaseOrder').objects.select_related('status').get(pk=order.pk).status.code, trade.code)
        connection.check_constraints()
