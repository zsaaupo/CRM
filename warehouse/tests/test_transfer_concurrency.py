from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import sleep

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from common.models import ProductStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from warehouse.api_views import WarehouseTransferUpdateAPIView
from warehouse.models import Stock, WareHouse, WarehouseTransfer, WarehouseTransferItem
from warehouse.services import TransferBusy, complete_transfer


class TransferConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(username='transfer-race', email='race@example.com')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        unit = UnitOfMeasure.objects.create(name='Pieces', code='TRANSFER-RACE-PCS')
        self.product = Product.objects.create(name='Race product', sku='TRANSFER-RACE', unit=unit, currency_id='BDT')
        self.source = WareHouse.objects.create(name='Source', address='Dhaka', phone='1')
        self.destination = WareHouse.objects.create(name='Destination', address='Dhaka', phone='2')
        Stock.objects.create(warehouse=self.source, product=self.product, quantity=5)
        self.record = self.transfer(2)

    def transfer(self, quantity):
        record = WarehouseTransfer.objects.create(source_warehouse=self.source, destination_warehouse=self.destination)
        WarehouseTransferItem.objects.create(transfer=record, line_number=1, product=self.product, unit=self.product.unit, quantity=quantity)
        return record

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
                    except TransferBusy:
                        if attempt == 19:
                            raise
                        sleep(0.02)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(worker, actions))

    def assert_conserved(self):
        balances = list(Stock.objects.filter(product=self.product).values_list('quantity', flat=True))
        self.assertEqual(sum(balances), Decimal('5'))
        self.assertTrue(all(value >= 0 for value in balances))

    def test_simultaneous_retries_move_stock_once(self):
        action = lambda actor: complete_transfer(actor, self.record.pk)[1]
        self.assertEqual(sorted(self.race([action, action])), [False, True])
        self.assertEqual(Stock.objects.get(warehouse=self.source).quantity, Decimal('3'))
        self.assertEqual(Stock.objects.filter(warehouse=self.destination, product=self.product).count(), 1)
        self.assert_conserved()

    def test_two_transfers_cannot_overdraw_shared_source_stock(self):
        other = self.transfer(4)
        def complete(pk):
            def action(actor):
                try:
                    complete_transfer(actor, pk)
                    return 'completed'
                except ValidationError:
                    return 'shortage'
            return action
        self.assertEqual(sorted(self.race([complete(self.record.pk), complete(other.pk)])), ['completed', 'shortage'])
        self.assertEqual(WarehouseTransfer.objects.filter(is_completed=True).count(), 1)
        self.assert_conserved()

    def test_edit_racing_completion_preserves_the_completed_quantity(self):
        def edit(actor):
            item = self.record.items.get()
            request = APIRequestFactory().patch('/transfer/update/', {'items': [{'id': item.pk, 'line_number': 1,
                'product': self.product.pk, 'unit': self.product.unit_id, 'quantity': '4'}]}, format='json')
            force_authenticate(request, actor)
            response = WarehouseTransferUpdateAPIView.as_view()(request, pk=self.record.pk)
            if response.status_code == 200:
                return 'edited'
            self.assertEqual(response.status_code, 409, response.data)
            if getattr(response.data.get('detail'), 'code', None) == 'transfer_busy':
                raise TransferBusy()
            return 'read-only'
        result = self.race([lambda actor: complete_transfer(actor, self.record.pk)[1], edit])
        self.assertTrue(result[0])
        self.record.refresh_from_db()
        self.assertEqual(Stock.objects.get(warehouse=self.source).quantity, Decimal('5') - self.record.items.get().quantity)
        self.assertEqual(Stock.objects.get(warehouse=self.destination).quantity, self.record.items.get().quantity)
        self.assert_conserved()
