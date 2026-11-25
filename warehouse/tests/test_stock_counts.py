import csv
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO, StringIO
from threading import Barrier
from time import sleep
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from audit_log.models import AuditLog
from common.models import ProductStatus
from inventory.models import Brand, Currency, Product, ProductCategory, ProductType, UnitOfMeasure
from warehouse.models import Stock, StockAdjustment, StockCount, WareHouse
from warehouse.stock_count_files import CountEntry
from warehouse.stock_counts import CountConflict, create_stock_count, finalize_stock_count
from .helpers import assign_warehouse_role


class StockCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='counter')
        cls.main = WareHouse.objects.create(name='Main')
        cls.other = WareHouse.objects.create(name='Other')
        cls.unit = UnitOfMeasure.objects.create(name='Pieces', code='COUNT-PCS')
        cls.category = ProductCategory.objects.create(name='Fresh foods', code='fresh')
        cls.brand = Brand.objects.create(name='Local')
        cls.rice = cls.add_product('00120', 10, category=cls.category, brand=cls.brand)
        cls.oil = cls.add_product('OIL', 2, category=cls.category, variant_of=cls.rice)
        cls.add_product('DRAFT', 12, status_id='draft')
        Stock.objects.create(warehouse=cls.other, product=cls.rice, quantity=30)

    @classmethod
    def add_product(cls, sku, quantity, **fields):
        product = Product.objects.create(
            name=f'Product {sku}', sku=sku, unit=cls.unit, cost_price=Decimal('2.5001'), currency_id='BDT',
            **{'status_id': 'active', **fields},
        )
        Stock.objects.create(warehouse=cls.main, product=product, quantity=quantity)
        return product

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.admin)

    def create_count(self, **fields):
        response = self.client.post(reverse('warehouse:stock-count-list'), {
            'warehouse': self.main.pk, 'count_type': 'full', **fields,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['stock_count']['id']

    def finalize(self, count_id, rice='11', oil='1', **fields):
        return self.client.post(reverse('warehouse:stock-count-finalize', args=[count_id]), {
            'items': [{'sku': '00120', 'counted_qty': rice}, {'sku': 'oil', 'counted_qty': oil}], **fields,
        }, format='json')

    def report(self, count_id):
        return self.client.get(reverse('warehouse:stock-count-report', args=[count_id]))

    def test_snapshot_filters_and_blind_download(self):
        zero = self.add_product('ZERO', 0, category=self.category, brand=self.brand)
        count_id = self.create_count(count_type='partial', category=self.category.pk, brand=self.brand.pk,
                                     blind_count=True)
        count = StockCount.objects.get(pk=count_id)
        self.assertEqual({line.product_id for line in count.items.all()}, {self.rice.pk, zero.pk})
        Product.objects.filter(pk=self.rice.pk).update(sku='RENAMED', cost_price=50)
        Stock.objects.filter(warehouse=self.main, product=self.rice).update(quantity=17)
        rows = self.client.get(reverse('warehouse:stock-count-detail', args=[count_id])).data['items']
        self.assertEqual([row['sku'] for row in rows], ['00120', 'ZERO'])
        self.assertTrue(all(row['expected_qty'] is None for row in rows))
        file = self.client.get(reverse('warehouse:stock-count-initial-file', args=[count_id]))
        csv_rows = list(csv.DictReader(StringIO(file.content.decode('utf-8-sig'))))
        self.assertEqual([row['Expected'] for row in csv_rows], ['', ''])
        self.assertEqual([row['Counted'] for row in csv_rows], ['', ''])
        self.assertEqual(self.report(count_id).status_code, 404)
        self.assertEqual(self.client.get(reverse('warehouse:stock-count-final-file', args=[count_id])).status_code, 404)
        result = self.client.post(reverse('warehouse:stock-count-finalize', args=[count_id]), {
            'items': [{'sku': '00120', 'counted_qty': 12}, {'sku': 'ZERO', 'counted_qty': 0}],
        }, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.report(count_id).data['items'][0]['variance_value'], '5.0002')
        self.assertTrue(self.report(count_id).data['items'][0]['stock_moved'])

    def test_csv_reorders_rows_and_keeps_zero_as_a_count(self):
        count_id = self.create_count()
        url = reverse('warehouse:stock-count-finalize', args=[count_id])
        header = 'Product Name, Product Code, IMEI or Serial Numbers, Expected, Counted\r\n'

        def upload(lines):
            file = SimpleUploadedFile('counted.csv', (header + lines).encode('utf-8'))
            return self.client.post(url, {'file': file}, format='multipart')

        for lines in (
            'Oil,OIL,,2,\r\n"Rice, small",00120,null,10,12',
            'Oil,OIL,,2,0\r\nRice,00120,null,11,12',
            'Oil,OIL,,2,0\r\nOil,oil,,2,2',
            'Oil,OIL,,2,0\r\nRice,UNKNOWN,,10,12',
        ):
            result = upload(lines)
            self.assertEqual(result.status_code, 400, result.data)
            self.assertFalse(StockCount.objects.get(pk=count_id).items.filter(counted_qty__isnull=False).exists())
        result = upload('Oil,OIL,,2,0\r\n"Rice, small",00120,null,10,12')
        self.assertEqual(result.status_code, 200, result.data)
        report = self.report(count_id).data
        self.assertEqual(report['summary']['quantities'][0], {
            'unit': self.unit.code, 'expected': '12', 'counted': '12',
            'overage': '2', 'shortage': '2', 'variance': '0',
        })
        self.assertEqual(report['summary']['values'], [{'currency': 'BDT', 'variance': '0'}])
        self.assertEqual(report['items'][1]['counted_qty'], '0')
        self.assertFalse(StockAdjustment.objects.exists())
        self.assertFalse(self.finalize(count_id).data['finalized_now'])
        self.assertEqual(self.report(count_id).data['items'][0]['counted_qty'], '12')
        self.assertTrue(AuditLog.objects.filter(resource='warehouse.stockcount', action='finalized',
                                               object_id=str(count_id)).exists())

    def test_posts_add_and_subtract_once_and_blocks_overlapping_count(self):
        first, second = self.create_count(), self.create_count()
        posted = self.finalize(first, post_adjustment=True)
        self.assertEqual(posted.status_code, 200, posted.data)
        adjustment = StockCount.objects.get(pk=first).adjustment
        self.assertTrue(adjustment.is_completed)
        self.assertEqual(list(adjustment.items.values_list('operation', 'quantity')), [('add', 1), ('subtract', 1)])
        self.assertEqual(Stock.objects.get(warehouse=self.main, product=self.rice).quantity, 11)
        self.assertEqual(Stock.objects.get(warehouse=self.main, product=self.oil).quantity, 1)
        self.assertEqual(Stock.objects.get(warehouse=self.other, product=self.rice).quantity, 30)
        self.assertFalse(self.finalize(first, post_adjustment=True).data['finalized_now'])
        conflict = self.finalize(second, post_adjustment=True)
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(StockCount.objects.get(pk=second).status, 'pending')
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_moves_that_net_to_zero_still_block_posting(self):
        count_id = self.create_count()
        stock = Stock.objects.get(warehouse=self.main, product=self.rice)
        for quantity in (9, 10):
            stock.quantity = quantity
            stock.save(update_fields=['quantity'])
        result = self.finalize(count_id, post_adjustment=True)
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(self.finalize(count_id).status_code, 200)
        self.assertTrue(self.report(count_id).data['items'][0]['stock_moved'])
        self.assertFalse(StockAdjustment.objects.exists())

    def test_final_report_drafts_each_difference_once_even_when_net_variance_is_zero(self):
        count_id = self.create_count()
        self.assertEqual(self.finalize(count_id).status_code, 200)
        count = StockCount.objects.get(pk=count_id)
        finalized_at = count.finalized_at
        url = reverse('warehouse_pages:stock-count-report', args=[count_id])
        self.assertContains(self.client.get(url), 'Create Draft Adjustment')
        self.assertFalse(StockAdjustment.objects.exists())

        response = self.client.post(url, follow=True)
        self.assertContains(response, 'Draft stock adjustment created.')
        self.assertContains(response, 'Draft adjustment')
        self.assertNotContains(response, 'Create Draft Adjustment')
        count.refresh_from_db()
        adjustment = count.adjustment
        self.assertFalse(adjustment.is_completed)
        self.assertIsNone(adjustment.completed_at)
        self.assertContains(response, reverse('warehouse_pages:adjustment-detail', args=[adjustment.pk]))
        self.assertEqual(list(adjustment.items.values_list('operation', 'quantity')), [('add', 1), ('subtract', 1)])
        self.assertEqual(Stock.objects.get(warehouse=self.main, product=self.rice).quantity, 10)
        self.assertEqual(Stock.objects.get(warehouse=self.main, product=self.oil).quantity, 2)
        self.assertEqual(count.finalized_at, finalized_at)
        self.assertRedirects(self.client.post(url), url)
        self.assertEqual(StockAdjustment.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(resource='warehouse.stockcount', object_id=str(count_id),
                                                action='adjustment_drafted').count(), 1)
        complete = self.client.post(reverse('warehouse:adjustment-complete'), {'adjustment_id': adjustment.pk}, format='json')
        self.assertEqual(complete.status_code, 200, complete.data)
        self.assertEqual(Stock.objects.get(warehouse=self.main, product=self.rice).quantity, 11)
        self.assertEqual(Stock.objects.get(warehouse=self.main, product=self.oil).quantity, 1)
        self.assertContains(self.client.get(url), 'Posted adjustment')

    def test_final_report_omits_adjustment_action_when_counts_match(self):
        count_id = self.create_count()
        self.assertEqual(self.finalize(count_id, rice='10', oil='2').status_code, 200)
        url = reverse('warehouse_pages:stock-count-report', args=[count_id])
        self.assertNotContains(self.client.get(url), 'Create Draft Adjustment')
        self.assertContains(self.client.post(url), 'quantities match for every SKU', status_code=400)
        self.assertFalse(StockAdjustment.objects.exists())

    def test_linked_draft_checks_stock_movement_when_completed(self):
        count_id = self.create_count()
        self.assertEqual(self.finalize(count_id).status_code, 200)
        stock = Stock.objects.get(warehouse=self.main, product=self.rice)
        stock.quantity = 8
        stock.save(update_fields=['quantity'])
        url = reverse('warehouse_pages:stock-count-report', args=[count_id])
        self.assertRedirects(self.client.post(url), url)
        adjustment = StockCount.objects.get(pk=count_id).adjustment
        result = self.client.post(reverse('warehouse:adjustment-complete'), {'adjustment_id': adjustment.pk}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertIn('Start a fresh count', str(result.data['items']))
        adjustment.refresh_from_db()
        self.assertFalse(adjustment.is_completed)
        self.assertEqual(Stock.objects.get(pk=stock.pk).quantity, 8)
        self.assertEqual(Stock.objects.get(warehouse=self.main, product=self.oil).quantity, 2)

    def test_final_report_adjustment_requires_stock_adjustment_permissions_and_warehouse_scope(self):
        count_id = self.create_count()
        hidden_id = self.create_count(warehouse=self.other.pk)
        self.assertEqual(self.finalize(count_id).status_code, 200)
        self.assertEqual(self.finalize(hidden_id, items=[{'sku': '00120', 'counted_qty': '31'}]).status_code, 200)
        user = get_user_model().objects.create_user(username='report-viewer')
        role = assign_warehouse_role(user, warehouse=self.main).role
        permissions = Permission.objects.filter(content_type__app_label='warehouse')
        role.permissions.add(permissions.get(codename='view_stockcount'))
        self.client.force_login(user)
        url = reverse('warehouse_pages:stock-count-report', args=[count_id])
        self.assertNotContains(self.client.get(url), 'Create Draft Adjustment')
        self.assertEqual(self.client.post(url).status_code, 403)
        role.permissions.add(permissions.get(codename='add_stockadjustment'))
        hidden = reverse('warehouse_pages:stock-count-report', args=[hidden_id])
        self.assertEqual(self.client.post(hidden).status_code, 404)
        self.assertContains(self.client.get(url), 'Create Draft Adjustment')
        self.assertRedirects(self.client.post(url), url)
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_linked_draft_stays_in_its_warehouse_and_can_be_discarded(self):
        count_id = self.create_count()
        self.assertEqual(self.finalize(count_id).status_code, 200)
        report_url = reverse('warehouse_pages:stock-count-report', args=[count_id])
        self.assertRedirects(self.client.post(report_url), report_url)
        adjustment_id = StockCount.objects.get(pk=count_id).adjustment_id
        detail_url = reverse('warehouse:adjustment-detail', args=[adjustment_id])
        changed = self.client.patch(detail_url, {'warehouse': self.other.pk}, format='json')
        self.assertEqual(changed.status_code, 400, changed.data)
        self.assertEqual(self.client.delete(detail_url).status_code, 204)
        self.assertIsNone(StockCount.objects.get(pk=count_id).adjustment_id)
        self.assertContains(self.client.get(report_url), 'Create Draft Adjustment')

    def test_adjustment_failure_rolls_back_all_stock_count_changes(self):
        count_id = self.create_count()
        new_unit = UnitOfMeasure.objects.create(name='Box', code='COUNT-BOX')
        Product.objects.filter(pk=self.oil.pk).update(unit=new_unit)
        result = self.finalize(count_id, post_adjustment=True)
        self.assertEqual(result.status_code, 400, result.data)
        count = StockCount.objects.get(pk=count_id)
        self.assertEqual(count.status, 'pending')
        self.assertFalse(count.items.filter(counted_qty__isnull=False).exists())
        self.assertFalse(StockAdjustment.objects.exists())
        self.assertEqual(Stock.objects.get(warehouse=self.main, product=self.rice).quantity, 10)

    def test_warehouse_scope_and_permission(self):
        own = self.create_count()
        hidden = self.create_count(warehouse=self.other.pk)
        user = get_user_model().objects.create_user(username='branch-counter')
        role = assign_warehouse_role(user, warehouse=self.main).role
        role.permissions.set(Permission.objects.filter(content_type__app_label='warehouse',
                             codename__in=['view_stockcount', 'add_stockcount', 'change_stockcount']))
        self.client.force_login(user)
        listing = self.client.get(reverse('warehouse:stock-count-list'), {'page_size': 'all'}).data
        self.assertEqual([count['id'] for count in listing['results']], [own])
        self.assertEqual(self.finalize(own, post_adjustment=True).status_code, 403)
        self.assertEqual(self.finalize(hidden).status_code, 404)
        file = reverse('warehouse:stock-count-initial-file', args=[hidden])
        self.assertEqual(self.client.get(file).status_code, 404)
        self.assertEqual(self.client.get(reverse('warehouse_pages:stock-count-finalize', args=[hidden])).status_code, 404)

    def test_pages_and_list_exports(self):
        add_url = reverse('warehouse_pages:add-stock-count')
        self.assertContains(self.client.get(add_url), 'Blind count')
        response = self.client.post(add_url, {'warehouse': self.main.pk, 'count_type': 'full'})
        self.assertRedirects(response, reverse('warehouse_pages:stock-count-list'))
        count = StockCount.objects.get()
        list_url = reverse('warehouse_pages:stock-count-list')
        self.assertContains(self.client.get(list_url), count.reference_no)
        self.assertContains(self.client.get(list_url, {'q': 'irrelevant'}), 'No stock counts found')
        finalize_url = reverse('warehouse_pages:stock-count-finalize', args=[count.pk])
        self.assertContains(self.client.get(finalize_url), 'Download initial CSV')
        response = self.client.post(finalize_url, {
            'input_method': 'inline',
            **{f'count_{item.pk}': '0' for item in count.items.all()},
        })
        self.assertRedirects(response, reverse('warehouse_pages:stock-count-report', args=[count.pk]))
        self.assertContains(self.client.get(list_url), 'Final Report')
        result = self.client.get(list_url, {'export': 'csv', 'column': 'warehouse'})
        rows = list(csv.reader(StringIO(result.content.decode('utf-8-sig'))))
        self.assertEqual(rows, [['Reference', 'Warehouse'], [count.reference_no, 'Main']])
        xlsx = self.client.get(list_url, {'export': 'xlsx'})
        with ZipFile(BytesIO(xlsx.content)) as workbook:
            sheet = workbook.read('xl/worksheets/sheet1.xml')
            self.assertIn(count.reference_no.encode(), sheet)
        with patch('warehouse.stock_count_pages.render_pdf', return_value=b'%PDF-1.7') as render:
            self.assertEqual(self.client.get(list_url, {'export': 'pdf'}).content, b'%PDF-1.7')
            self.assertIn(count.reference_no, render.call_args.args[0]['rows'][0])


class StockCountConcurrencyTests(TransactionTestCase):
    def test_simultaneous_finalizations_post_one_adjustment(self):
        actor = get_user_model().objects.create_superuser(username='count-race')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='active', defaults={'name': 'Active'})
        warehouse = WareHouse.objects.create(name='Main')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='COUNT-RACE-PCS')
        product = Product.objects.create(name='Rice', sku='COUNT-RACE', unit=unit, currency_id='BDT', status_id='active')
        Stock.objects.create(warehouse=warehouse, product=product, quantity=10)
        count = create_stock_count(actor, warehouse_id=warehouse.pk, count_type='full')
        entries = [CountEntry(1, product.sku, Decimal('12'))]
        barrier = Barrier(2)

        def finish():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                for attempt in range(20):
                    try:
                        return finalize_stock_count(actor, count.pk, entries, post_adjustment=True)[1]
                    except CountConflict:
                        if attempt == 19:
                            raise
                        sleep(0.02)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(finish), pool.submit(finish)
            self.assertEqual(sorted([first.result(), second.result()]), [False, True])
        self.assertEqual(Stock.objects.get(warehouse=warehouse, product=product).quantity, 12)
        self.assertEqual(StockAdjustment.objects.count(), 1)
