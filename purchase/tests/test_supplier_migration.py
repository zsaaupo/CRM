from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SupplierMigrationTests(TransactionTestCase):
    before = [('purchase', '0001_initial'), ('people', '0003_party_image')]

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_legacy_names_migrate_without_guessing_party_identity_and_can_roll_back(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.addCleanup(self.restore)
        apps = executor.loader.project_state(self.before).apps
        Party = apps.get_model('people', 'Party')
        Order = apps.get_model('purchase', 'PurchaseOrder')
        apps.get_model('inventory', 'Currency').objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': '1'})
        apps.get_model('common', 'Status').objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        existing = Party.all_objects.create(code='EXISTING', name='Existing supplier', is_supplier=True)
        customer = Party.all_objects.create(code='CUSTOMER', name='Customer name', is_customer=True)
        deleted = Party.all_objects.create(code='DELETED', name='Deleted name', is_supplier=True, is_deleted=True)
        ambiguous = [Party.all_objects.create(code=f'AMB-{i}', name='Ambiguous name', is_supplier=True) for i in range(2)]
        names = ['Existing supplier', 'New supplier', 'New supplier', 'Customer name', 'Deleted name', 'Ambiguous name', None, '']
        orders = [Order.objects.create(supplier=name, currency_id='BDT', exchange_rate='1',
                                       create_by='original', modified_by='original') for name in names]
        Party.all_objects.create(code=f'LEGACY-SUP-{orders[1].pk}', name='Code collision', is_supplier=True)
        self.restore()
        executor = MigrationExecutor(connection)
        migrated = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        NewOrder = migrated.get_model('purchase', 'PurchaseOrder')
        NewParty = migrated.get_model('people', 'Party')
        linked = [NewOrder.objects.select_related('supplier').get(pk=order.pk) for order in orders]
        self.assertEqual(linked[0].supplier_id, existing.pk)
        self.assertEqual(linked[1].supplier_id, linked[2].supplier_id)
        self.assertEqual(linked[1].supplier.code, f'LEGACY-SUP-{orders[1].pk}-2')
        self.assertNotEqual(linked[3].supplier_id, customer.pk)
        self.assertNotEqual(linked[4].supplier_id, deleted.pk)
        self.assertNotIn(linked[5].supplier_id, [party.pk for party in ambiguous])
        self.assertFalse(NewParty.all_objects.get(pk=customer.pk).is_supplier)
        self.assertTrue(NewParty.all_objects.get(pk=deleted.pk).is_deleted)
        for old, new in zip(orders, linked):
            if old.supplier:
                self.assertEqual(new.supplier.name, old.supplier)
                self.assertTrue(new.supplier.is_supplier)
                self.assertFalse(new.supplier.is_deleted)
            else:
                self.assertIsNone(new.supplier_id)
            for field in ['order_number', 'created_at', 'updated_at', 'create_by', 'modified_by']:
                self.assertEqual(getattr(new, field), getattr(old, field))
        executor.migrate(self.before)
        restored_apps = executor.loader.project_state(self.before).apps
        OldOrder = restored_apps.get_model('purchase', 'PurchaseOrder')
        for old in orders:
            self.assertEqual(OldOrder.objects.get(pk=old.pk).supplier, old.supplier or None)
