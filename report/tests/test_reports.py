from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem
from sale.models import Chalan, ChalanItem, PackingSlip, SalesOrder, SalesOrderItem, SalesQuotation, SalesQuotationItem
from warehouse.models import Stock, WareHouse, WarehouseTransfer, WarehouseTransferItem
from warehouse.tests.helpers import assign_warehouse_role
from report.catalog import REPORTS
from report.demo import demo_document
from report.rendering import asset_fetcher, render_pdf
from report.services import document_context, money, snapshot


class ReportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='report-reader')
        self.access = assign_warehouse_role(self.user, all_warehouses=True)
        self.grant(*(name for spec in REPORTS.values() for name in spec.permissions))
        self.source = WareHouse.objects.create(name='Source', address='Dhaka', phone='1')
        self.destination = WareHouse.objects.create(name='Destination', address='Dhaka', phone='2')
        self.foreign = WareHouse.objects.create(name='Foreign', address='Dhaka', phone='3')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='REPORT-PCS')
        self.product = Product.objects.create(name='Report product', sku='REPORT-ITEM', unit=unit, currency_id='BDT', sale_price=9999)
        self.party = Party.objects.create(code='REPORT-PARTY', name='Report party', is_supplier=True, is_customer=True, address='Current party address')
        self.purchase = PurchaseOrder.objects.create(supplier=self.party, warehouse=self.source, currency_id='BDT', exchange_rate=1, shipping_cost=2)
        self.sale = SalesOrder.objects.create(customer=self.party, warehouse=self.source, currency_id='BDT', exchange_rate=1,
            shipping_cost=2, billing_address='Saved billing address', shipping_address='Saved shipping address')
        self.quotation = SalesQuotation.objects.create(
            customer=self.party, warehouse=self.source, currency_id='BDT', exchange_rate=1,
            status=SalesQuotation.Status.ISSUED, issued_at=timezone.now(), shipping_cost=2)
        self.transfer = WarehouseTransfer.objects.create(source_warehouse=self.source, destination_warehouse=self.destination)
        self.chalan = Chalan.objects.create(customer=self.party, warehouse=self.source,
            shipping_address='Saved Chalan shipping address', notes='Internal Chalan notes', shipping_cost=2)
        ChalanItem.objects.create(chalan=self.chalan, line_number=1, product=self.product, unit=unit,
            quantity='1.500001', unit_price=10, discount_amount='0.5000', tax_rate=15, description='Packing instruction')
        self.packing_slip = PackingSlip.objects.create(chalan=self.chalan)
        for model, parent in [(PurchaseOrderItem, {'purchase_order': self.purchase}), (SalesOrderItem, {'sales_order': self.sale}),
                              (SalesQuotationItem, {'quotation': self.quotation})]:
            model.objects.create(**parent, line_number=1, product=self.product, unit=unit, quantity='1.5', unit_price=10,
                discount_amount='0.5', tax_rate=15, description='Order description snapshot')
        WarehouseTransferItem.objects.create(transfer=self.transfer, line_number=1, product=self.product, unit=unit, quantity='1.500001')
        self.quotation.refresh_from_db()
        self.quotation.issued_document = document_context('quotation', self.quotation)
        self.quotation.save(update_fields=['issued_document'])
        Stock.objects.create(warehouse=self.source, product=self.product, quantity=20)
        self.client = APIClient(enforce_csrf_checks=True); self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        self.records = {'purchase': self.purchase, 'sale': self.sale, 'transfer': self.transfer,
                        'quotation': self.quotation, 'packing-slip': self.packing_slip}

    def grant(self, *names):
        self.access.role.permissions.set([Permission.objects.get(content_type__app_label=name.split('.')[0], codename=name.split('.')[1]) for name in set(names)])

    def url(self, kind, pk=None):
        return reverse(f'report:{kind}-pdf', args=[pk if pk is not None else self.records[kind].pk])

    def pdf(self, kind):
        response = self.client.get(self.url(kind), HTTP_ACCEPT='application/pdf')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        data = b''.join(response.streaming_content); response.close()
        return response, PdfReader(BytesIO(data)), data

    def test_real_pdf_endpoints_download_all_document_types_without_mutations(self):
        before = {kind: (getattr(record, REPORTS[kind].completed_field), record.updated_at)
                  for kind, record in self.records.items()}
        stock_before = list(Stock.objects.values())
        for kind in self.records:
            with self.subTest(kind=kind):
                response, reader, data = self.pdf(kind)
                self.assertTrue(data.startswith(b'%PDF-'))
                self.assertEqual(response['Content-Type'], 'application/pdf')
                self.assertIn('attachment;', response['Content-Disposition'])
                self.assertIn('no-store', response['Cache-Control']); self.assertIn('private', response['Cache-Control'])
                text = '\n'.join(page.extract_text() for page in reader.pages)
                self.assertIn('REPORT-ITEM', text)
                self.assertIn(REPORTS[kind].title, text)
                self.assertIn('1.500001' if kind in ('transfer', 'packing-slip') else '1.5', text)
                if kind != 'transfer':
                    priced = self.chalan if kind == 'packing-slip' else self.records[kind]
                    self.assertIn(money(priced.grand_total), text)
                    self.assertIn('10.0000', text); self.assertNotIn('9,999.0000', text)
                self.records[kind].refresh_from_db()
                record = self.records[kind]
                self.assertEqual(before[kind], (getattr(record, REPORTS[kind].completed_field), record.updated_at))
        self.assertEqual(stock_before, list(Stock.objects.values()))

    @override_settings(REPORT_COMPANY={'name': 'Example company'})
    def test_packing_slip_reprints_use_current_chalan_and_respect_warehouse_scope(self):
        ChalanItem.objects.filter(chalan=self.chalan).update(quantity='7.510001')
        self.chalan.shipping_address = 'Updated delivery address'
        self.chalan.save(update_fields=['shipping_address'])
        _, reader, _ = self.pdf('packing-slip')
        text = '\n'.join(page.extract_text() for page in reader.pages)
        for value in (self.packing_slip.slip_number, self.chalan.chalan_number, 'Updated delivery address',
                      '7.510001', 'Packing instruction', 'Packed by', 'Checked by', 'Unit price', '15%',
                      '10.0000', '0.5000', '11.1900', '85.7900', 'BDT 74.6000', 'BDT 2.0000', 'BDT 87.7900'):
            self.assertIn(value, ' '.join(text.split()))
        for value in ('Internal Chalan notes', '9,999.0000'):
            self.assertNotIn(value, text)
        self.access.all_warehouses = False
        self.access.warehouse = self.destination
        self.access.save()
        self.assertEqual(self.client.get(self.url('packing-slip')).status_code, 404)

    def test_missing_role_permissions_and_direct_user_grants_cannot_download(self):
        for kind, spec in REPORTS.items():
            for permission in spec.permissions:
                self.grant(*(name for name in spec.permissions if name != permission))
                self.user.user_permissions.add(Permission.objects.get(content_type__app_label=permission.split('.')[0], codename=permission.split('.')[1]))
                with patch('report.views.render_pdf') as render:
                    response = self.client.get(self.url(kind), HTTP_ACCEPT='application/pdf')
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response['Content-Type'], 'application/json')
                    render.assert_not_called()
        self.assertEqual(APIClient().get(self.url('purchase'), HTTP_ACCEPT='application/pdf').status_code, 403)

    def test_missing_scope_and_out_of_scope_records_are_denied_before_rendering(self):
        self.access.all_warehouses = False; self.access.warehouse = self.destination; self.access.save()
        with patch('report.views.render_pdf', return_value=b'%PDF-placeholder'):
            self.assertEqual(self.client.get(self.url('purchase')).status_code, 404)
            self.assertEqual(self.client.get(self.url('sale')).status_code, 404)
            self.assertEqual(self.client.get(self.url('transfer')).status_code, 200)
            self.access.warehouse = self.source; self.access.save()
            self.assertEqual(self.client.get(self.url('transfer')).status_code, 200)
            self.access.warehouse = self.foreign; self.access.save()
            self.assertEqual(self.client.get(self.url('transfer')).status_code, 404)
        self.access.delete()
        self.assertEqual(self.client.get(self.url('purchase')).status_code, 403)

    def test_superusers_can_download_without_role_and_completed_documents_remain_available(self):
        root = get_user_model().objects.create_superuser(username='report-root', email='root@example.com')
        self.client.force_login(root)
        PurchaseOrder.objects.filter(pk=self.purchase.pk).update(is_received=True)
        SalesOrder.objects.filter(pk=self.sale.pk).update(is_completed=True)
        WarehouseTransfer.objects.filter(pk=self.transfer.pk).update(is_completed=True)
        with patch('report.views.render_pdf', return_value=b'%PDF-placeholder'):
            for kind in self.records:
                self.assertEqual(self.client.get(self.url(kind)).status_code, 200)

    def test_missing_records_unsupported_methods_and_renderer_failure(self):
        for kind in self.records:
            self.assertEqual(self.client.get(self.url(kind, 999999)).status_code, 404)
            self.assertEqual(self.client.post(self.url(kind), {}).status_code, 405)
            self.assertEqual(self.client.options(self.url(kind)).status_code, 200)
        with patch('report.rendering.pdf_engine', side_effect=OSError('missing native library')):
            with self.assertLogs('report.rendering', level='ERROR'):
                response = self.client.get(self.url('purchase'), HTTP_ACCEPT='application/pdf')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_all_lines_are_included_beyond_api_page_size_and_totals_match(self):
        SalesOrderItem.objects.bulk_create([SalesOrderItem(sales_order=self.sale, line_number=index,
            product=self.product, unit=self.product.unit, quantity=1, unit_price=1, description=f'UNIQUE-LINE-{index:03}')
            for index in range(2, 36)])
        _, reader, _ = self.pdf('sale')
        text = '\n'.join(page.extract_text() for page in reader.pages)
        self.assertGreater(len(reader.pages), 1)
        self.assertIn('UNIQUE-LINE-035', text)
        self.assertEqual(text.count('REPORT-ITEM'), 35)
        self.assertIn(money(self.sale.grand_total), text)

    def test_nullable_parties_historical_soft_deleted_parties_and_addresses(self):
        self.party.delete()
        document = snapshot('sale', self.sale.pk, self.user)
        self.assertEqual(document['cards'][0]['name'], 'Report party')
        self.assertEqual(document['addresses'][0][1], 'Saved billing address')
        self.purchase.supplier = None; self.purchase.warehouse = None; self.purchase.save()
        document = snapshot('purchase', self.purchase.pk, self.user)
        self.assertEqual([card['name'] for card in document['cards']], ['Not specified', 'Not specified'])

    def test_snapshot_is_materialized_before_rendering_and_has_no_per_line_queries(self):
        baseline = len(connection.atomic_blocks)
        def render(document):
            self.assertEqual(len(connection.atomic_blocks), baseline)
            self.assertIsInstance(document['rows'], list)
            return b'%PDF-placeholder'
        with patch('report.views.render_pdf', side_effect=render):
            self.assertEqual(self.client.get(self.url('sale')).status_code, 200)
        with CaptureQueriesContext(connection) as first:
            snapshot('sale', self.sale.pk, self.user)
        SalesOrderItem.objects.bulk_create([SalesOrderItem(sales_order=self.sale, line_number=index,
            product=self.product, unit=self.product.unit, quantity=1, unit_price=1) for index in range(2, 25)])
        with CaptureQueriesContext(connection) as second:
            snapshot('sale', self.sale.pk, self.user)
        self.assertEqual(len(first), len(second))

    def test_download_actions_are_visible_only_with_full_document_permissions(self):
        for namespace, prefix, kind in [('purchase_pages', 'purchase', 'purchase'), ('sale_pages', 'sale', 'sale'),
                                         ('warehouse_pages', 'transfer', 'transfer')]:
            listing = self.client.get(reverse(f'{namespace}:{prefix}-list'))
            self.assertContains(listing, f'data-report-url-template="{self.url(kind, 0)}"')
            detail = self.client.get(reverse(f'{namespace}:{prefix}-detail', args=[self.records[kind].pk]))
            self.assertContains(detail, f'data-report-url="{self.url(kind)}"')
        self.grant('purchase.view_purchaseorder')
        self.assertNotContains(self.client.get(reverse('purchase_pages:purchase-detail', args=[self.purchase.pk])), 'data-report-download')

    def test_local_asset_policy_and_template_escaping(self):
        fetch = asset_fetcher({'logo_path': ''})
        for url in ['https://example.com/x', 'file:///C:/Windows/win.ini', 'report-asset:///../../models.py', 'data:text/plain,hello']:
            with self.assertRaises(ValueError):
                fetch(url)
        self.purchase.notes = '<img src="https://example.com/secret">'; self.purchase.save()
        document = snapshot('purchase', self.purchase.pk, self.user)
        html = render_to_string('report/document.html', {'report': document})
        self.assertIn('&lt;img', html)
        self.assertNotIn('<img src="https://example.com', html)


class ReportLayoutTests(TestCase):
    def test_long_packing_slip_keeps_signatures_with_the_last_product(self):
        reader = PdfReader(BytesIO(render_pdf(demo_document('packing-slip', line_count=42))))
        self.assertGreater(len(reader.pages), 1)
        last_page = reader.pages[-1].extract_text()
        self.assertIn('Product / SKU', last_page)
        self.assertIn('ITEM-042', last_page)
        self.assertIn('Grand total', last_page)
        self.assertIn('Packed by', last_page)

    def test_long_reports_repeated_headings_and_unicode_fonts(self):
        document = demo_document('sale', line_count=42)
        reader = PdfReader(BytesIO(render_pdf(document)))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        self.assertGreater(len(reader.pages), 1)
        self.assertEqual(text.count('ITEM-042'), 1)
        self.assertGreater(text.count('Unit price'), 1)
        self.assertIn('Grand total', text)
        self.assertIn('Page 1 of', text)

    @override_settings(REPORT_COMPANY={'name': 'Configured company'})
    def test_company_branding_is_shared_across_types(self):
        for kind in REPORTS:
            self.assertEqual(demo_document(kind)['company']['name'], 'Configured company')
