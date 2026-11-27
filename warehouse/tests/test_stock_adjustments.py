from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from warehouse.models import Stock, StockMovement, WareHouse, WarehouseTransfer, WarehouseTransferItem
from warehouse.services import complete_transfer


class StockAdjustmentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='adjuster')
        self.client = APIClient()
        self.client.force_login(self.user)
        self.branch = WareHouse.objects.create(name='Main')
        unit = UnitOfMeasure.objects.create(code='ADJ', name='Unit')
        self.product = Product.objects.create(sku='ADJ-P', name='Parts', unit=unit, currency_id='BDT')
        self.url = reverse('warehouse:adjustments')

    def data(self, quantity, cost=None):
        return dict(request_key=str(uuid4()), warehouse=self.branch.pk, product=self.product.pk,
                    action='add', quantity=quantity, declared_unit_cost=cost, reason='Verified correction')

    def test_adjustments_replay_weighted_cost_and_transfer_carry(self):
        data = self.data(2, '10')
        self.assertEqual(self.client.post(self.url, data, format='json').status_code, 201)
        self.assertEqual(self.client.post(self.url, data, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, dict(data, quantity=3), format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, self.data(6, '18'), format='json').status_code, 201)
        stock = Stock.objects.get()
        self.assertEqual((stock.quantity, stock.average_cost_base), (8, 16))
        destination = WareHouse.objects.create(name='Branch')
        transfer = WarehouseTransfer.objects.create(source_warehouse=self.branch, destination_warehouse=destination)
        WarehouseTransferItem.objects.create(transfer=transfer, line_number=1, product=self.product,
                                            unit=self.product.unit, quantity=2)
        complete_transfer(self.user, transfer.pk, action='dispatch')
        self.assertEqual(self.client.post(self.url, self.data(6, '24'), format='json').status_code, 201)
        complete_transfer(self.user, transfer.pk, action='receive')
        self.assertEqual(Stock.objects.get(warehouse=destination).average_cost_base, Decimal('16'))
        self.assertEqual(Stock.objects.get(warehouse=self.branch).average_cost_base, Decimal('20'))
        self.assertEqual(StockMovement.objects.get(kind='transfer_in').unit_cost_base, Decimal('16'))
        self.assertContains(self.client.get(reverse('warehouse_pages:adjustments')), 'Verified correction')

    def test_shortage_and_mixed_unknown_costs(self):
        self.assertEqual(self.client.post(self.url, self.data(2), format='json').status_code, 201)
        self.assertEqual(self.client.post(self.url, self.data(2, '10'), format='json').status_code, 201)
        self.assertIsNone(Stock.objects.get().average_cost_base)
        self.assertEqual(self.client.post(self.url, dict(self.data(5), action='subtract'), format='json').status_code, 400)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(self.client.post(self.url, dict(self.data(4), action='subtract'), format='json').status_code, 201)
        self.assertEqual(self.client.post(self.url, self.data(3, '9.125'), format='json').status_code, 201)
        self.assertEqual(Stock.objects.get().average_cost_base, Decimal('9.125'))
