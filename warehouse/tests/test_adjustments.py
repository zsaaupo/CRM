from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import sleep

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.models import ProductStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from warehouse.adjustments import AdjustmentBusy, complete_adjustment
from warehouse.models import Stock, StockAdjustment, WareHouse
from .helpers import assign_warehouse_role


class StockAdjustmentTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(username='adjuster', email='adjuster@example.com')
        self.main = WareHouse.objects.create(name='Main')
        self.other = WareHouse.objects.create(name='Other')
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='ADJ-PCS')
        self.rice = Product.objects.create(name='Rice', sku='ADJ-RICE', unit=self.unit, currency_id='BDT')
        self.oil = Product.objects.create(name='Oil', sku='ADJ-OIL', unit=self.unit, currency_id='BDT')
        Stock.objects.create(warehouse=self.main, product=self.rice, quantity=10)
        Stock.objects.create(warehouse=self.other, product=self.rice, quantity=30)
        self.client = APIClient()
        self.client.force_login(self.actor)

    def line(self, product, quantity, operation='add', **fields):
        return {'product': product.pk, 'quantity': quantity, 'operation': operation, **fields}

    def draft(self, *items, **fields):
        response = self.client.post(reverse('warehouse:adjustment-list'), {'items': items, **fields}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['stock_adjustment']

    def complete(self, pk):
        return self.client.post(reverse('warehouse:adjustment-complete'), {'adjustment_id': pk}, format='json')

    def stock(self, product, warehouse=None):
        return Stock.objects.filter(warehouse=warehouse or self.main, product=product).values_list('quantity', flat=True).first()

    def test_completion_applies_stock_once_for_products_without_suppliers(self):
        record = self.draft(self.line(self.rice, '5'),
                            self.line(self.rice, '2', 'subtract'), self.line(self.oil, '0.125001'),
                            warehouse=self.main.pk, is_completed=True)
        self.assertEqual(record['status_label'], 'Draft')
        self.assertNotIn('supplier', record['items'][0])
        self.assertEqual(record['items'][0]['unit'], self.unit.pk)
        self.assertEqual(self.stock(self.rice), 10)
        self.assertIsNone(self.stock(self.oil))

        response = self.complete(record['id'])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['completed_now'])
        posted = response.data['stock_adjustment']
        self.assertEqual(posted['status_label'], 'Completed')
        self.assertIsNotNone(posted['completed_at'])
        for line in posted['items']:
            self.assertTrue({'supplier', 'supplier_name'}.isdisjoint(line))
        self.assertEqual(self.stock(self.rice), 13)
        self.assertEqual(self.stock(self.oil), Decimal('0.125001'))
        self.assertEqual(self.stock(self.rice, self.other), 30)

        retry = self.complete(record['id'])
        self.assertFalse(retry.data['completed_now'])
        self.assertEqual(self.stock(self.rice), 13)
        page = self.client.get(reverse('warehouse_pages:adjustment-detail', args=[record['id']]))
        self.assertNotContains(page, '<th scope="col">Supplier</th>')
        detail = reverse('warehouse:adjustment-detail', args=[record['id']])
        self.assertEqual(self.client.patch(detail, {'notes': 'edited'}, format='json').status_code, 409)
        self.assertEqual(self.client.delete(detail).status_code, 409)

    def test_shortage_rolls_back_an_earlier_addition_and_completion(self):
        record = self.draft(self.line(self.rice, '1'), self.line(self.oil, '2', 'subtract'), warehouse=self.main.pk)
        response = self.complete(record['id'])
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(self.oil.sku, str(response.data['quantity']))
        self.assertEqual(self.stock(self.rice), 10)
        self.assertIsNone(self.stock(self.oil))
        draft = StockAdjustment.objects.get(pk=record['id'])
        self.assertFalse(draft.is_completed)
        self.assertIsNone(draft.completed_at)

    def test_stock_limit_failure_keeps_the_draft_and_balance(self):
        stock = Stock.objects.get(warehouse=self.main, product=self.rice)
        Stock.objects.filter(pk=stock.pk).update(quantity=Decimal('999999999999'))
        record = self.draft(self.line(self.rice, '2'), warehouse=self.main.pk)
        response = self.complete(record['id'])
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('quantity limit', str(response.data['quantity']))
        self.assertEqual(self.stock(self.rice), Decimal('999999999999'))
        self.assertFalse(StockAdjustment.objects.get(pk=record['id']).is_completed)

    def test_validation_and_draft_line_reordering(self):
        record = self.draft(self.line(self.rice, '3'), self.line(self.oil, '2', 'subtract'), warehouse=self.main.pk)
        first, second = record['items']
        url = reverse('warehouse:adjustment-update', args=[record['id']])
        for items in ([], [{'id': first['id']}], [self.line(self.rice, '0')], [self.line(self.rice, '-1')],
                      [self.line(self.rice, '0.0000001')], [self.line(self.rice, '1', 'multiply')],
                      [self.line(self.rice, '1', id=999999)]):
            response = self.client.patch(url, {'items': items}, format='json')
            self.assertEqual(response.status_code, 400, response.data)

        response = self.client.patch(url, {'items': [self.line(self.oil, '5', 'subtract', id=second['id']),
                                                    self.line(self.rice, '3', id=first['id'])]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([(line['id'], line['line_number']) for line in response.data['items']],
                         [(second['id'], 1), (first['id'], 2)])
        self.assertEqual(self.stock(self.rice), 10)
        detail = reverse('warehouse:adjustment-detail', args=[record['id']])
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertFalse(StockAdjustment.objects.filter(pk=record['id']).exists())

    def test_warehouse_scope_applies_on_pages_options_and_writes(self):
        hidden = self.draft(self.line(self.rice, '1'), warehouse=self.other.pk)
        user = get_user_model().objects.create_user(username='branch-adjuster')
        access = assign_warehouse_role(user, warehouse=self.main)
        access.role.permissions.set(Permission.objects.filter(content_type__app_label='warehouse',
                                    codename__in=['view_stockadjustment', 'add_stockadjustment']))
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse('warehouse_pages:adjustment-list')), 'Stock adjustments')
        page = self.client.get(reverse('warehouse_pages:add-adjustment'))
        self.assertContains(page, 'data-adjustment-form')
        self.assertNotContains(page, 'data-line-supplier-label')
        options = self.client.get(reverse('warehouse:adjustment-options')).data
        self.assertEqual([choice['value'] for choice in options['warehouse']], [self.main.pk])
        self.assertEqual(set(options['product'][0]), {'value', 'label', 'unit_label'})
        self.assertEqual(self.client.get(reverse('warehouse:adjustment-list-view')).data['count'], 0)
        self.assertEqual(self.client.get(reverse('warehouse_pages:adjustment-detail', args=[hidden['id']])).status_code, 404)
        self.assertEqual(self.client.get(reverse('warehouse:adjustment-detail', args=[hidden['id']])).status_code, 404)
        denied = self.client.post(reverse('warehouse:adjustment-list'),
                                  {'warehouse': self.other.pk, 'items': [self.line(self.rice, '1')]}, format='json')
        self.assertEqual(denied.status_code, 403)
        own = self.draft(self.line(self.rice, '1'))
        self.assertEqual(own['warehouse'], self.main.pk)
        self.assertEqual(self.complete(own['id']).status_code, 403)

        access.role.permissions.add(Permission.objects.get(content_type__app_label='warehouse', codename='change_stockadjustment'))
        edit = reverse('warehouse_pages:edit-adjustment', args=[own['id']])
        self.assertContains(self.client.get(edit), 'data-adjustment-form')
        self.assertEqual(self.complete(hidden['id']).status_code, 404)
        self.assertEqual(self.complete(own['id']).status_code, 200)


class AdjustmentConcurrencyTests(TransactionTestCase):
    def test_simultaneous_completion_creates_one_balance_and_applies_stock_once(self):
        actor = get_user_model().objects.create_superuser(username='adjustment-race', email='race@example.com')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        warehouse = WareHouse.objects.create(name='Main')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='ADJ-RACE-PCS')
        product = Product.objects.create(name='Rice', sku='ADJ-RACE', unit=unit, currency_id='BDT')
        record = StockAdjustment.objects.create(warehouse=warehouse)
        record.items.create(line_number=1, product=product, unit=unit, operation='add', quantity='2.000001')
        barrier = Barrier(2)

        def complete():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                for attempt in range(20):
                    try:
                        return complete_adjustment(actor, record.pk)[1]
                    except AdjustmentBusy:
                        if attempt == 19:
                            raise
                        sleep(0.02)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(complete)
            second = pool.submit(complete)
            self.assertEqual(sorted([first.result(), second.result()]), [False, True])
        self.assertEqual(Stock.objects.get(warehouse=warehouse, product=product).quantity, Decimal('2.000001'))
