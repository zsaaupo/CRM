from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from inventory.models import Product, UnitOfMeasure
from warehouse.models import Stock, WareHouse


class StockModelTests(TestCase):
    def setUp(self):
        self.warehouse = WareHouse.objects.create(name='Main warehouse', address='Dhaka', phone='01700000000')
        unit = UnitOfMeasure.objects.create(name='Kilograms', code='KG')
        self.product = Product.objects.create(name='Rice', sku='RICE', unit=unit, currency_id='BDT')

    def stock(self, **overrides):
        return Stock(warehouse=self.warehouse, product=self.product,
                     create_by='tester', modified_by='tester', **overrides)

    def test_default_zero_and_fractional_quantities(self):
        stock = self.stock()
        self.assertEqual(stock.quantity, Decimal('0'))
        stock.quantity = Decimal('12.123456')
        stock.full_clean()
        stock.save()
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('12.123456'))
        self.assertEqual(self.warehouse.stocks.get(), stock)
        self.assertEqual(self.product.stocks.get(), stock)

    def test_warehouse_product_pair_is_unique_but_other_warehouses_are_allowed(self):
        self.stock().save()
        with self.assertRaises(ValidationError):
            self.stock().full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.stock().save()
        branch = WareHouse.objects.create(name='Branch', address='Gazipur', phone='01800000000')
        Stock.objects.create(warehouse=branch, product=self.product, quantity='5')
        self.assertEqual(Stock.objects.count(), 2)

    def test_negative_quantities_are_rejected_by_validation_and_database(self):
        with self.assertRaises(ValidationError):
            self.stock(quantity=Decimal('-0.000001')).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.stock(quantity=Decimal('-0.000001')).save()

    def test_referenced_product_and_warehouse_cannot_be_deleted(self):
        self.stock().save()
        for related in [self.product, self.warehouse]:
            with self.assertRaises(ProtectedError):
                related.delete()
