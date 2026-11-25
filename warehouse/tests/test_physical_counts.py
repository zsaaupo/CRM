from decimal import Decimal
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from warehouse.models import Stock, StockMovement, WareHouse


class PhysicalCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(username='count-operator')
        cls.branch = WareHouse.objects.create(name='Main')
        cls.unit = UnitOfMeasure.objects.create(code='CNT', name='Count unit')
        cls.product = Product.objects.create(name='Item', sku='COUNT-A', currency_id='BDT', unit=cls.unit)

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.user)

    def start(self):
        data = dict(request_key=str(uuid4()), warehouse=self.branch.pk, full_count=False, products=[self.product.pk])
        result = self.client.post(reverse('warehouse:count-list'), data, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        repeat = self.client.post(reverse('warehouse:count-list'), data, format='json')
        self.assertEqual(repeat.data['id'], result.data['id'])
        return result.data['id']

    def post_count(self, pk, quantity):
        return self.client.post(reverse('warehouse:count-finalize', args=[pk]),
                                {'lines': [{'product': self.product.pk, 'quantity': quantity}]}, format='json')

    def test_adjustment_reconciles_and_concurrent_count_requires_recount(self):
        first, stale = self.start(), self.start()
        self.assertEqual(self.post_count(first, '7.25').status_code, 200)
        self.assertEqual(self.post_count(first, '7.25').status_code, 200)
        self.assertEqual(self.post_count(first, '7').status_code, 409)
        self.assertEqual(self.post_count(stale, '5').status_code, 409)
        self.assertEqual(Stock.objects.get().quantity, Decimal('7.25'))
        self.assertEqual(StockMovement.objects.get().quantity, Decimal('7.25'))
        out = StringIO()
        call_command('reconcile_stock', stdout=out)
        self.assertIn('Reconciled 1', out.getvalue())
        self.assertContains(self.client.get(reverse('warehouse_pages:count-detail', args=[first])), 'Finalized')
