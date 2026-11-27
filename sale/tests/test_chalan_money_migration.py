from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings


class ChalanMoneyMigrationTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def restore(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    @override_settings(INVENTORY_BASE_CURRENCY='EUR')
    def test_existing_chalan_items_and_slip_keep_links_without_catalog_repricing(self):
        self.addCleanup(self.restore)
        old = self.migrate([('sale', '0010_packing_slip')])
        currency, _ = old.get_model('inventory', 'Currency').objects.get_or_create(code='BDT', defaults={'name': 'Taka'})
        old.get_model('inventory', 'ProductType').objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        old.get_model('common', 'ProductStatus').objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        unit = old.get_model('inventory', 'UnitOfMeasure').objects.create(code='MIG-PCS', name='Pieces')
        product = old.get_model('inventory', 'Product').objects.create(sku='MIG-PRICE', name='Product', unit=unit,
            currency=currency, sale_price='29.9999', product_tax=15)
        customer = old.get_model('people', 'Party').objects.create(code='MIG-CUSTOMER', name='Customer', is_customer=True)
        warehouse = old.get_model('warehouse', 'WareHouse').objects.create(name='Migration warehouse')
        chalan = old.get_model('sale', 'Chalan').objects.create(customer=customer, warehouse=warehouse,
            chalan_number='CH-MIGRATION', shipping_address='Saved address')
        item = old.get_model('sale', 'ChalanItem').objects.create(chalan=chalan, line_number=1, product=product,
            unit=unit, quantity='2.500001')
        slip = old.get_model('sale', 'PackingSlip').objects.create(chalan=chalan, slip_number='PS-MIGRATION')

        new = self.migrate([('sale', '0011_chalan_money')])
        migrated = new.get_model('sale', 'Chalan').objects.get(pk=chalan.pk)
        self.assertEqual((migrated.currency_id, migrated.exchange_rate, migrated.shipping_cost),
                         ('EUR', Decimal('1'), Decimal('0')))
        self.assertEqual((migrated.chalan_number, migrated.shipping_address, migrated.updated_at),
                         (chalan.chalan_number, chalan.shipping_address, chalan.updated_at))
        line = migrated.items.get(pk=item.pk)
        self.assertEqual(line.quantity, Decimal('2.500001'))
        self.assertEqual((line.unit_price, line.discount_amount, line.tax_rate), (0, 0, 0))
        self.assertEqual((migrated.packing_slip.pk, migrated.packing_slip.slip_number), (slip.pk, slip.slip_number))
        connection.check_constraints()
