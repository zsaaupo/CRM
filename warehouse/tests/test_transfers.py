from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from inventory.models import Product, UnitOfMeasure
from warehouse.models import Stock, WareHouse, WarehouseTransfer, WarehouseTransferItem


class WarehouseTransferTests(TestCase):
    def setUp(self):
        self.source = WareHouse.objects.create(name='Source', address='Dhaka', phone='1')
        self.destination = WareHouse.objects.create(name='Destination', address='Dhaka', phone='2')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='TRANSFER-PCS')
        self.product = Product.objects.create(sku='TRANSFER-ITEM', name='Transfer product', unit=unit, currency_id='BDT')
        self.stock = Stock.objects.create(warehouse=self.source, product=self.product, quantity='10')

    def transfer(self, **changes):
        return WarehouseTransfer(source_warehouse=self.source, destination_warehouse=self.destination,
            create_by='tester', modified_by='tester', **changes)

    def item(self, transfer, **changes):
        return WarehouseTransferItem(transfer=transfer, line_number=1, product=self.product, unit=self.product.unit,
            quantity=Decimal('2.500001'), create_by='tester', modified_by='tester', **changes)

    def test_saving_transfer_preserves_stock_and_generates_distinct_references(self):
        first = self.transfer(); first.full_clean(); first.save()
        item = self.item(first); item.full_clean(); item.save()
        second = self.transfer(); second.save()
        self.assertNotEqual(first.transfer_number, second.transfer_number)
        self.assertTrue(first.transfer_number.startswith('WT-'))
        self.assertFalse(first.is_completed)
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('2.500001'))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))
        self.assertFalse(Stock.objects.filter(warehouse=self.destination, product=self.product).exists())

    def test_same_warehouse_is_rejected_by_validation_and_database(self):
        transfer = self.transfer()
        transfer.destination_warehouse = self.source
        with self.assertRaises(ValidationError) as error:
            transfer.full_clean()
        self.assertIn('destination_warehouse', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            transfer.save()

    def test_nonpositive_quantities_cannot_bypass_model_validation(self):
        transfer = self.transfer(); transfer.save()
        for quantity in [Decimal('0'), Decimal('-1')]:
            with self.subTest(quantity=quantity):
                item = self.item(transfer); item.quantity = quantity
                with self.assertRaises(ValidationError):
                    item.full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    item.save()

    def test_transfer_references_protect_warehouses_and_product(self):
        transfer = self.transfer(); transfer.save(); self.item(transfer).save()
        # Remove stock so protection must come from the transfer itself.
        self.stock.delete()
        for record in [self.source, self.destination, self.product]:
            with self.subTest(record=record):
                with self.assertRaises(ProtectedError):
                    record.delete()
