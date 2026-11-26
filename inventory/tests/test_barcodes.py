from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from ..barcodes import PRESETS, encode
from ..models import Product, UnitOfMeasure


class BarcodeGeometryTests(SimpleTestCase):
    def test_all_stock_presets_fit_their_page(self):
        for key, (_, paper) in PRESETS.items():
            with self.subTest(key=key):
                expected_capacity = int(key.split('_')[0]) if key.endswith('_per_sheet') else 1
                self.assertEqual(paper.capacity, expected_capacity)
                last = paper.position(paper.capacity - 1)
                self.assertLessEqual(last['left'] + paper.label_width, paper.sheet_width)
                self.assertLessEqual(last['top'] + paper.label_height, paper.sheet_height)
        avery = PRESETS['30_per_sheet'][1]
        self.assertEqual(avery.position(0), {'left': Decimal('4.7625'), 'top': Decimal('12.7')})
        self.assertEqual(avery.position(1)['left'] - avery.position(0)['left'], Decimal('69.85'))

    def test_retail_auto_checks_the_check_digit_and_keeps_leading_zeroes(self):
        for code in ('012345678905', '4006381333931'):
            with self.subTest(code=code):
                symbol = encode(code, True, Decimal(70), Decimal(30))
                self.assertEqual(''.join(group['text'] for group in symbol['digits']), code)
                self.assertGreaterEqual(symbol['bars'][0]['x'], Decimal('2.5'))
                self.assertNotIn('caption', symbol)
        self.assertEqual(encode('012345678904', True, Decimal(70), Decimal(30))['caption'], '012345678904')
        self.assertEqual(encode('012345678905', False, Decimal(70), Decimal(30))['caption'], '012345678905')
        with self.assertRaisesMessage(ValueError, 'does not fit'):
            encode('4006381333931', True, Decimal(70), Decimal(12))


class BarcodePrintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='label-reader')
        cls.unit = UnitOfMeasure.objects.create(code='LABEL-PCS', name='Pieces')
        cls.product = Product.objects.create(
            sku='LABEL01', name='Blue notebook', barcode='BOOK1', unit=cls.unit, currency_id='BDT',
            sale_price=Decimal('95'), promo_price=Decimal('70'),
            promo_start_date=date(2026, 11, 26), promo_end_date=date(2026, 11, 26),
        )
        cls.second = Product.objects.create(sku='PEN1', name='Blue pen', unit=cls.unit, currency_id='BDT')

    def setUp(self):
        self.page = reverse('inventory_pages:print-barcodes')
        self.search = reverse('inventory:barcode-search')
        self.print_url = reverse('inventory:print-barcodes')
        self.reader = APIClient(enforce_csrf_checks=True)
        self.reader.force_login(self.user)

    def grant_access(self):
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label='inventory', codename='view_product',
        ))
        page = self.reader.get(self.page)
        self.assertContains(page, 'Generate preview')
        self.reader.credentials(HTTP_X_CSRFTOKEN=self.reader.cookies['csrftoken'].value)

    def job(self, quantity=1, preset='30_per_sheet', **label_config):
        return {
            'items': [{'product_id': self.product.pk, 'quantity': quantity}],
            'label_config': label_config,
            'paper_config': {'preset': preset, 'custom': None},
        }

    def test_permission_and_csrf_match_inventory(self):
        self.assertEqual(self.reader.get(self.page).status_code, 403)
        self.assertEqual(self.reader.get(self.search, {'q': 'blue'}).status_code, 403)
        self.assertEqual(self.reader.post(self.print_url, self.job(), format='json').status_code, 403)
        self.grant_access()
        self.assertContains(self.reader.get(reverse('inventory_pages:product-list')), self.page)
        self.reader.credentials()
        self.assertEqual(self.reader.post(self.print_url, self.job(), format='json').status_code, 403)

    def test_search_uses_three_fields_and_leaves_exact_collisions_for_the_operator(self):
        self.grant_access()
        self.second.barcode = self.product.sku
        self.second.save(update_fields=['barcode'])
        for query in ('blue', 'LABE', 'BOOK1'):
            with self.subTest(query=query):
                hits = self.reader.get(self.search, {'q': query}).json()
                self.assertIn(self.product.pk, [hit['id'] for hit in hits])
        options = self.reader.get(self.search, {'q': 'label01', 'exact': 1}).json()
        self.assertEqual({option['id'] for option in options}, {self.product.pk, self.second.pk})
        self.assertEqual(self.reader.get(self.search, {'q': 'B'}).json(), [])
        self.assertEqual(self.reader.get(self.search, {'q': 'Blue', 'exact': 1}).json(), [])

    def test_jobs_validate_before_rendering(self):
        self.grant_access()
        missing = self.job()
        missing['items'][0]['product_id'] = 999999
        duplicates = self.job()
        duplicates['items'] *= 2
        past_limit = self.job(500)
        past_limit['items'].append({'product_id': self.second.pk, 'quantity': 1})
        impossible = self.job(preset='custom')
        impossible['paper_config']['custom'] = dict.fromkeys(
            ('sheet_width', 'sheet_height', 'label_width', 'label_height', 'margin_top',
             'margin_bottom', 'margin_left', 'margin_right', 'gap_x', 'gap_y'), 1,
        )
        for job in (missing, duplicates, past_limit, impossible, self.job(0), self.job(1.5),
                    self.job(preset='custom'), self.job(price_size=37)):
            with self.subTest(job=job):
                response = self.reader.post(self.print_url, job, format='json', HTTP_ACCEPT='application/pdf')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.headers['Content-Type'], 'application/json')

    def test_scanners_reporting_upc_as_ean_can_find_the_original_code(self):
        self.grant_access()
        self.product.barcode = '012345678905'
        self.product.save(update_fields=['barcode'])
        results = self.reader.get(self.search, {'q': '0012345678905', 'exact': 1}).json()
        self.assertEqual([item['code'] for item in results], ['012345678905'])
        self.second.barcode = '0012345678905'
        self.second.save(update_fields=['barcode'])
        results = self.reader.get(self.search, {'q': '012345678905', 'exact': 1}).json()
        self.assertEqual({item['id'] for item in results}, {self.product.pk, self.second.pk})

    @patch('inventory.barcode_views.render_pdf', return_value=b'%PDF-test')
    def test_print_document_paginates_queue_order_and_uses_current_label_prices(self, render):
        self.grant_access()
        job = self.job(quantity=30)
        job['items'].append({'product_id': self.second.pk, 'quantity': 2})
        with patch('inventory.barcodes.timezone.localdate', return_value=date(2026, 11, 26)):
            response = self.reader.post(self.print_url, job, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-test')
        pages = render.call_args.args[0]['pages']
        self.assertEqual([len(page) for page in pages], [30, 2])
        self.assertEqual(pages[1][0]['left'], pages[0][0]['left'])
        self.assertEqual(pages[1][0]['top'], pages[0][0]['top'])
        first = pages[0][0]['label']
        self.assertEqual(first['code'], 'BOOK1')
        self.assertTrue(first['price']['text'].endswith('95.00'))
        self.assertTrue(first['promo_price']['text'].endswith('70.00'))
        self.assertEqual(pages[1][0]['label']['code'], 'PEN1')
        self.assertNotIn('promo_price', pages[1][0]['label'])
        self.assertFalse(self.product.has_active_promo(date(2026, 11, 27)))
        self.assertFalse(self.product.has_active_promo(date(2026, 11, 25)))

    def test_real_pdf_generation(self):
        self.grant_access()
        job = self.job(preset='continuous_31_25', price_size=10, promo_price_size=10)
        with patch('inventory.barcodes.timezone.localdate', return_value=date(2026, 11, 26)):
            response = self.reader.post(self.print_url, job, format='json')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertEqual(response.headers['Content-Type'], 'application/pdf')
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))

    def test_price_text_that_would_be_clipped_returns_a_product_error(self):
        self.grant_access()
        job = self.job(preset='continuous_31_25', price_size=36)
        response = self.reader.post(self.print_url, job, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('BOOK1', response.json()['items'])
        self.assertIn('price text does not fit', response.json()['items'])

    def test_product_updates_validate_promotion_dates_and_can_clear_the_promotion(self):
        self.grant_access()
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label='inventory', codename='change_product',
        ))
        product_url = reverse('inventory:product-update', args=[self.product.pk])
        response = self.reader.patch(product_url, {'promo_end_date': '2026-11-25'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('promo_end_date', response.json())
        response = self.reader.patch(product_url, {'promo_price': None, 'promo_start_date': None,
                                                   'promo_end_date': None}, format='json')
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertFalse(self.product.has_active_promo(date(2026, 11, 26)))
