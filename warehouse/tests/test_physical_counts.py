import csv
from decimal import Decimal
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
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

    def sheet(self, pk, quantity):
        response = self.client.get(reverse('warehouse:count-sheet', args=[pk]))
        self.assertEqual(response.status_code, 200)
        header, row = list(csv.reader(StringIO(response.content.decode('utf-8'))))
        row[-1] = quantity
        return [header, row]

    def upload(self, rows):
        stream = StringIO()
        csv.writer(stream).writerows(rows)
        return SimpleUploadedFile('count.csv', stream.getvalue().encode('utf-8-sig'), 'text/csv')

    def test_sheet_preview_confirm_and_replay(self):
        pk = self.start()
        url = reverse('warehouse_pages:count-detail', args=[pk])
        rows = self.sheet(pk, '4.123456')
        response = self.client.post(url, {'action': 'preview', 'file': self.upload(rows)})
        self.assertContains(response, 'Finalize reviewed count')
        confirmation = response.context['confirmation']
        self.assertFalse(StockMovement.objects.exists())
        for _ in range(2):
            self.assertRedirects(self.client.post(url, {'confirmation': confirmation}), url)
        self.assertEqual(StockMovement.objects.get().quantity, Decimal('4.123456'))
        exported = self.client.get(reverse('warehouse:count-sheet', args=[pk])).content.decode()
        self.assertEqual(list(csv.reader(StringIO(exported)))[1][-1], '4.123456')

    def test_sheet_rejects_foreign_missing_duplicate_and_invalid_lines(self):
        pk = self.start()
        other = self.start()
        url = reverse('warehouse:count-sheet', args=[pk])
        header, row = self.sheet(pk, '0')
        for rows in ([header], [header, row, row], [header, [str(other), *row[1:]]],
                     [header, [*row[:-1], '-1']], [header, [*row[:3], 'wrong', *row[4:]]]):
            result = self.client.post(url, {'file': self.upload(rows)}, format='multipart')
            self.assertEqual(result.status_code, 400, result.data)
        response = self.client.post(url, {'file': self.upload([header, row])}, format='multipart')
        self.assertEqual(response.data['lines'][0]['quantity'], '0.000000')
        self.assertFalse(StockMovement.objects.exists())

    def test_post_preview_stock_change_requires_recount(self):
        first, second = self.start(), self.start()
        url = reverse('warehouse_pages:count-detail', args=[first])
        result = self.client.post(url, {'action': 'preview', 'file': self.upload(self.sheet(first, '7'))})
        confirmation = result.context['confirmation']
        self.assertEqual(self.post_count(second, '2').status_code, 200)
        self.assertEqual(self.client.post(url, {'confirmation': confirmation}).status_code, 409)
        self.assertEqual(Stock.objects.get().quantity, Decimal('2'))

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
