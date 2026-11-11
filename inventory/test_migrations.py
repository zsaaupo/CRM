from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProductLookupMigrationTests(TransactionTestCase):
    old_target = [('inventory', '0002_brand_create_by_brand_modified_by_product_create_by_and_more')]
    new_target = [('inventory', '0003_producttype_status_and_more')]

    def restore_schema(self):
        MigrationExecutor(connection).migrate(self.new_target)

    def test_legacy_codes_and_sales_tax_are_preserved(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old_target)
        self.addCleanup(self.restore_schema)
        old_apps = executor.loader.project_state(self.old_target).apps
        Unit = old_apps.get_model('inventory', 'UnitOfMeasure')
        OldProduct = old_apps.get_model('inventory', 'Product')
        unit = Unit.objects.create(name='Pieces', code='PCS')
        cases = [('goods', 'active'), ('service', 'discontinued'),
                 ('digital', 'draft'), ('goods', 'archived')]
        ids = []
        for index, (product_type, status) in enumerate(cases):
            product = OldProduct.objects.create(
                sku=f'LEGACY-{index}', name=f'Legacy {index}', currency='BDT', unit=unit,
                product_type=product_type, status=status, track_inventory=product_type == 'goods',
                sale_tax_rate=Decimal('15.5'),
            )
            ids.append(product.pk)

        executor = MigrationExecutor(connection)
        executor.migrate(self.new_target)
        apps = executor.loader.project_state(self.new_target).apps
        Product = apps.get_model('inventory', 'Product')
        ProductType = apps.get_model('inventory', 'ProductType')
        Status = apps.get_model('inventory', 'Status')
        self.assertEqual(ProductType.objects.count(), 3)
        self.assertEqual(Status.objects.count(), 4)
        for pk, (type_code, status_code) in zip(ids, cases):
            product = Product.objects.select_related('product_type', 'status').get(pk=pk)
            self.assertEqual(product.product_type.code, type_code)
            self.assertEqual(product.status.code, status_code)
            self.assertEqual(product.product_tax, Decimal('15.5'))
