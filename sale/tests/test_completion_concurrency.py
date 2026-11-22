from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import sleep

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from accounting.models import FinancialAccount, SalesPayment
from common.models import ProductStatus, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from sale.api_views import SalesOrderItemUpdateAPIView, SalesOrderUpdateAPIView
from sale.models import SalesOrder, SalesOrderItem
from sale.services import SaleBusy, complete_sale
from warehouse.models import Stock, WareHouse


class SaleCompletionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(username='complete-race', email='race@example.com')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='RACE-PCS')
        self.product = Product.objects.create(name='Rice', sku='RACE-RICE', unit=self.unit, currency_id='BDT')
        self.warehouse = WareHouse.objects.create(name='Main', address='Dhaka', phone='1')
        self.payment_account = FinancialAccount.objects.create(name='Sales bank', type='bank', warehouse=self.warehouse)
        self.stock = Stock.objects.create(warehouse=self.warehouse, product=self.product, quantity=5)
        self.order = self.new_order(2)

    def new_order(self, quantity):
        order = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=self.warehouse,
                                          payment_account=self.payment_account)
        SalesOrderItem.objects.create(sales_order=order, line_number=1, product=self.product, unit=self.unit,
                                       quantity=quantity, unit_price=1)
        return order

    def race(self, actions):
        barrier = Barrier(len(actions))
        def worker(action):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=10)
                for attempt in range(20):
                    try:
                        return action(actor)
                    except SaleBusy:
                        if attempt == 19:
                            raise
                        sleep(0.02)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(worker, actions))

    def test_simultaneous_completion_only_deducts_once(self):
        action = lambda actor: complete_sale(actor, self.order.pk)[1]
        self.assertEqual(sorted(self.race([action, action])), [False, True])
        self.assertEqual(Stock.objects.get().quantity, Decimal('3'))
        self.assertEqual(SalesPayment.objects.filter(sales_order=self.order).count(), 1)

    def test_two_sales_cannot_oversell_shared_stock(self):
        other = self.new_order(4)
        def complete(order_id):
            def action(actor):
                try:
                    complete_sale(actor, order_id)
                    return 'completed'
                except ValidationError:
                    return 'shortage'
            return action
        self.assertEqual(sorted(self.race([complete(self.order.pk), complete(other.pk)])), ['completed', 'shortage'])
        completed = SalesOrder.objects.get(is_completed=True)
        self.assertEqual(Stock.objects.get().quantity, Decimal('5') - completed.items.get().quantity)

    def check_edit_race(self, nested):
        item_id = self.order.items.get().pk
        def edit(actor):
            payload = {'quantity': '4'}
            view, pk = SalesOrderItemUpdateAPIView, item_id
            if nested:
                payload = {'items': [{'id': item_id, 'line_number': 1, 'product': self.product.pk,
                    'unit': self.unit.pk, 'quantity': '4', 'unit_price': '1'}]}
                view, pk = SalesOrderUpdateAPIView, self.order.pk
            request = APIRequestFactory().patch('/sale/update/', payload, format='json')
            force_authenticate(request, actor)
            response = view.as_view()(request, pk=pk)
            if response.status_code == 200:
                return 'edited'
            self.assertEqual(response.status_code, 409, response.data)
            if getattr(response.data.get('detail'), 'code', None) == 'sale_busy':
                raise SaleBusy()
            return 'read-only'
        result = self.race([lambda actor: complete_sale(actor, self.order.pk)[1], edit])
        self.assertTrue(result[0])
        self.assertIn(result[1], ['edited', 'read-only'])
        self.assertEqual(Stock.objects.get().quantity, Decimal('5') - SalesOrderItem.objects.get(pk=item_id).quantity)

    def test_item_edit_racing_completion_keeps_deducted_quantity_consistent(self):
        self.check_edit_race(nested=False)

    def test_nested_edit_racing_completion_keeps_deducted_quantity_consistent(self):
        self.check_edit_race(nested=True)
