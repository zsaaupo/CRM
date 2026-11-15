from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import sleep

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from common.models import ProductStatus, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.api_views import PurchaseOrderItemUpdateAPIView, PurchaseOrderUpdateAPIView
from purchase.services import PurchaseBusy, receive_purchase
from warehouse.models import Stock, WareHouse


class PurchaseReceiveConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(username='receive-race', email='race@example.com')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='RACE-PCS')
        self.product = Product.objects.create(name='Rice', sku='RACE-RICE', unit=self.unit, currency_id='BDT')
        self.warehouse = WareHouse.objects.create(name='Main', address='Dhaka', phone='1')
        self.order = self.new_order('2')

    def new_order(self, quantity):
        order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=self.warehouse)
        PurchaseOrderItem.objects.create(purchase_order=order, line_number=1, product=self.product, unit=self.unit,
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
                    except PurchaseBusy:
                        if attempt == 19:
                            raise
                        sleep(0.02)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(worker, actions))

    def test_simultaneous_receive_of_one_purchase_only_increases_stock_once(self):
        def receive(actor):
            return receive_purchase(actor, self.order.pk)[1]
        self.assertEqual(sorted(self.race([receive, receive])), [False, True])
        self.assertEqual(Stock.objects.get().quantity, Decimal('2'))

    def test_two_purchases_of_same_product_share_one_balance(self):
        other = self.new_order('3')
        self.assertEqual(self.race([
            lambda actor: receive_purchase(actor, self.order.pk)[1],
            lambda actor: receive_purchase(actor, other.pk)[1],
        ]), [True, True])
        self.assertEqual(Stock.objects.count(), 1)
        self.assertEqual(Stock.objects.get().quantity, Decimal('5'))

    def test_line_edit_racing_receipt_cannot_change_received_quantity(self):
        item_id = self.order.items.get().pk
        def edit(actor):
            request = APIRequestFactory().patch('/purchase/item/', {'quantity': '4'}, format='json')
            force_authenticate(request, actor)
            response = PurchaseOrderItemUpdateAPIView.as_view()(request, pk=item_id)
            if response.status_code == 200:
                return 'edited'
            self.assertEqual(response.status_code, 409, response.data)
            if getattr(response.data.get('detail'), 'code', None) == 'purchase_busy':
                raise PurchaseBusy()
            return 'read-only'
        results = self.race([lambda actor: receive_purchase(actor, self.order.pk)[1], edit])
        self.assertTrue(results[0])
        self.assertIn(results[1], ['edited', 'read-only'])
        item = PurchaseOrderItem.objects.get(pk=item_id)
        self.assertEqual(Stock.objects.get().quantity, item.quantity)
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_received)

    def test_nested_purchase_edit_racing_receipt_cannot_change_received_quantity(self):
        item_id = self.order.items.get().pk
        def edit(actor):
            request = APIRequestFactory().patch('/purchase/order/', {'items': [{
                'id': item_id, 'line_number': 1, 'product': self.product.pk, 'unit': self.unit.pk,
                'quantity': '4', 'unit_price': '1',
            }]}, format='json')
            force_authenticate(request, actor)
            response = PurchaseOrderUpdateAPIView.as_view()(request, pk=self.order.pk)
            if response.status_code == 200:
                return 'edited'
            self.assertEqual(response.status_code, 409, response.data)
            if getattr(response.data.get('detail'), 'code', None) == 'purchase_busy':
                raise PurchaseBusy()
            return 'read-only'
        results = self.race([lambda actor: receive_purchase(actor, self.order.pk)[1], edit])
        self.assertTrue(results[0])
        item = PurchaseOrderItem.objects.get(pk=item_id)
        self.assertEqual(Stock.objects.get().quantity, item.quantity)
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_received)
