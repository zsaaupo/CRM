from decimal import Decimal

from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TransferItemMigrationTests(TransactionTestCase):
    before = [('warehouse', '0006_warehouse_transfer')]
    after = [('warehouse', '0007_transfer_items')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection); executor.migrate(targets)
        loader = MigrationExecutor(connection).loader
        return loader.project_state(list(loader.applied_migrations)).apps

    def restore(self):
        executor = MigrationExecutor(connection); executor.migrate(executor.loader.graph.leaf_nodes())

    def fixture(self):
        self.addCleanup(self.restore)
        apps = self.migrate(self.before)
        apps.get_model('inventory', 'Currency').objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        apps.get_model('inventory', 'ProductType').objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        apps.get_model('common', 'ProductStatus').objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        unit = apps.get_model('inventory', 'UnitOfMeasure').objects.create(name='Pieces', code='MIG-TRANSFER')
        product = apps.get_model('inventory', 'Product').objects.create(name='Existing product', sku='MIG-TRANSFER', unit_id=unit.pk, currency_id='BDT')
        Warehouse = apps.get_model('warehouse', 'WareHouse')
        source = Warehouse.objects.create(name='Source', address='Dhaka', phone='1')
        destination = Warehouse.objects.create(name='Destination', address='Dhaka', phone='2')
        apps.get_model('warehouse', 'Stock').objects.create(warehouse_id=source.pk, product_id=product.pk, quantity='8.750000')
        record = apps.get_model('warehouse', 'WarehouseTransfer').objects.create(source_warehouse_id=source.pk,
            destination_warehouse_id=destination.pk, product_id=product.pk, quantity='1.250001',
            is_completed=True, notes='History', create_by='creator', modified_by='completer')
        return record, unit

    def test_existing_completed_transfer_moves_to_one_item_without_touching_stock(self):
        old, unit = self.fixture()
        apps = self.migrate(self.after)
        record = apps.get_model('warehouse', 'WarehouseTransfer').objects.get(pk=old.pk)
        item = apps.get_model('warehouse', 'WarehouseTransferItem').objects.get(transfer_id=old.pk)
        self.assertTrue(record.is_completed)
        self.assertEqual(record.transfer_number, old.transfer_number)
        self.assertEqual(item.product_id, old.product_id)
        self.assertEqual(item.unit_id, unit.pk)
        self.assertEqual(item.quantity, Decimal('1.250001'))
        self.assertEqual(item.line_number, 1)
        for field in ['created_at', 'updated_at', 'create_by', 'modified_by']:
            self.assertEqual(getattr(item, field), getattr(old, field))
        self.assertEqual(apps.get_model('warehouse', 'Stock').objects.get().quantity, Decimal('8.75'))
        old_apps = self.migrate(self.before)
        restored = old_apps.get_model('warehouse', 'WarehouseTransfer').objects.get(pk=old.pk)
        self.assertEqual(restored.product_id, old.product_id)
        self.assertEqual(restored.quantity, Decimal(old.quantity))
        connection.check_constraints()

    def test_reverse_refuses_to_discard_multiple_products(self):
        old, unit = self.fixture()
        apps = self.migrate(self.after)
        apps.get_model('warehouse', 'WarehouseTransferItem').objects.create(transfer_id=old.pk,
            line_number=2, product_id=old.product_id, unit_id=unit.pk, quantity=1)
        with self.assertRaises(IrreversibleError):
            self.migrate(self.before)
        # The failed schema rollback is atomic, preserving both lines.
        self.assertEqual(apps.get_model('warehouse', 'WarehouseTransferItem').objects.count(), 2)
        connection.check_constraints()
