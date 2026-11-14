from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from inventory.models import Currency, Product, UnitOfMeasure
from common.models import TradeStatus
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem
from warehouse.models import Stock


class PurchaseModelTests(TestCase):
    def setUp(self):
        self.supplier = Party.objects.create(code='SUP', name='Trading company', is_supplier=True)
        self.currency = Currency.objects.get(code='BDT')
        self.unit = UnitOfMeasure.objects.create(code='PCS', name='Pieces')
        self.product = Product.objects.create(sku='KEYBOARD', name='Keyboard', currency=self.currency, unit=self.unit)

    def order(self, **changes):
        return PurchaseOrder(supplier=self.supplier, currency=self.currency, exchange_rate=Decimal('1'),
                             create_by='tester', modified_by='tester', **changes)

    def item(self, order, **changes):
        values = {'purchase_order': order, 'line_number': 1, 'product': self.product, 'unit': self.unit,
                  'quantity': Decimal('2'), 'unit_price': Decimal('100'),
                  'create_by': 'tester', 'modified_by': 'tester'}
        values.update(changes)
        return PurchaseOrderItem(**values)

    def test_orders_and_items_validate_save_and_do_not_change_stock(self):
        order = self.order()
        order.full_clean()
        order.save()
        item = self.item(order, description='Keyboard purchase')
        item.full_clean()
        item.save()
        order.refresh_from_db()
        self.assertTrue(order.order_number.startswith('PO-'))
        self.assertEqual(order.status.code, 'draft')
        self.assertEqual(order.items.get(), item)
        self.assertEqual(order.subtotal, Decimal('200.0000'))
        self.assertEqual(order.grand_total, Decimal('200.0000'))
        self.assertEqual(str(order), order.order_number)
        self.assertFalse(Stock.objects.exists())

    def test_unique_order_references_and_line_numbers(self):
        order = self.order()
        order.save()
        another = self.order()
        another.save()
        self.assertNotEqual(order.order_number, another.order_number)
        duplicate = self.order()
        duplicate.order_number = order.order_number
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()
        self.item(order).save()
        with self.assertRaises(ValidationError):
            self.item(order).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item(order).save()
        self.item(order, line_number=2).save()
        self.item(another).save()

    def test_supplier_is_optional_party_reference(self):
        for value in [None, self.supplier]:
            order = self.order()
            order.supplier = value
            order.full_clean()
            order.save()
            order.refresh_from_db()
            self.assertEqual(order.supplier, value)

    def test_purchase_uses_shared_status_model_and_allows_new_status_codes(self):
        status = TradeStatus.objects.create(code='awaiting-approval', name='Awaiting approval')
        order = self.order()
        order.status = status
        order.full_clean()
        order.save()
        self.assertEqual(order.status_id, 'awaiting-approval')
        with self.assertRaises(ProtectedError):
            status.delete()
        order.status_id = 'missing-status'
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_date_rate_shipping_and_status_constraints(self):
        cases = [{'exchange_rate': 0}, {'exchange_rate': -1}, {'shipping_cost': -1}]
        for values in cases:
            order = self.order()
            for field, value in values.items():
                setattr(order, field, value)
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    order.full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    order.save()
        order = self.order()
        order.expected_date = order.order_date - timedelta(days=1)
        with self.assertRaises(ValidationError):
            order.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            order.save()
        order = self.order()
        order.exchange_rate = Decimal('2')
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_item_quantity_prices_discount_tax_and_line_constraints(self):
        order = self.order()
        order.save()
        cases = [{'quantity': 0}, {'quantity': -1}, {'unit_price': -1}, {'discount_amount': -1},
                 {'discount_amount': 201}, {'tax_rate': -1}, {'tax_rate': 101}, {'line_number': 0}]
        for values in cases:
            item = self.item(order, **values)
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    item.full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    item.save()
        with self.assertRaises(ValidationError):
            self.item(order, quantity=Decimal('1.1234567')).full_clean()

    def test_only_product_unit_is_supported(self):
        order = self.order()
        order.save()
        other_unit = UnitOfMeasure.objects.create(name='Boxes', code='BOX')
        with self.assertRaises(ValidationError):
            self.item(order, unit=other_unit).full_clean()

    def test_totals_recalculate_with_line_changes_and_round_at_four_decimal_places(self):
        order = self.order(shipping_cost=Decimal('5'))
        order.save()
        item = self.item(order, quantity=Decimal('3'), unit_price=Decimal('10'),
                         discount_amount=Decimal('2'), tax_rate=Decimal('15'))
        item.save()
        self.assertEqual(item.net_amount, Decimal('28.0000'))
        self.assertEqual(item.tax_amount, Decimal('4.2000'))
        self.assertEqual(item.line_total, Decimal('32.2000'))
        self.assertEqual(order.grand_total, Decimal('37.2000'))
        item.quantity = Decimal('4')
        item.save()
        self.assertEqual(order.grand_total, Decimal('48.7000'))
        item.delete()
        self.assertEqual(order.grand_total, Decimal('5'))
        tiny = self.item(order, quantity=Decimal('0.000001'), unit_price=Decimal('50'), tax_rate=Decimal('50'))
        self.assertEqual(tiny.net_amount, Decimal('0.0001'))
        self.assertEqual(tiny.tax_amount, Decimal('0.0001'))
        self.assertEqual(tiny.line_total, Decimal('0.0002'))
        large = self.item(order, quantity=Decimal('999999999999.999999'), unit_price=Decimal('99999999999999.9999'))
        self.assertGreater(large.line_total, Decimal('1E25'))

    def test_agreed_price_description_unit_and_rate_are_snapshots(self):
        usd = Currency.objects.create(code='USD', name='Dollar', exchange_rate='120')
        order = self.order()
        order.currency = usd
        order.exchange_rate = Decimal('120')
        order.save()
        item = self.item(order, description='Original keyboard')
        item.save()
        usd.exchange_rate = Decimal('125')
        usd.save()
        self.product.name = 'New keyboard name'
        self.product.cost_price = Decimal('500')
        self.product.save()
        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.exchange_rate, Decimal('120'))
        self.assertEqual(item.unit_price, Decimal('100'))
        self.assertEqual(item.description, 'Original keyboard')

    def test_references_are_protected(self):
        order = self.order()
        order.save()
        self.item(order).save()
        for obj in [self.currency, self.product, self.unit, order, order.status]:
            with self.subTest(model=type(obj).__name__):
                with self.assertRaises(ProtectedError):
                    obj.delete()
