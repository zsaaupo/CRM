from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import FinancialAccount
from inventory.models import Product, UnitOfMeasure
from people.models import Party
from report.services import snapshot
from sale.models import SalesOrder, SalesQuotation
from sale.services import ACTION_PERMISSIONS, QUOTE_ADD, QUOTE_DELETE, QUOTE_EDIT, QUOTE_VIEW
from warehouse.models import Stock, WareHouse
from warehouse.tests.helpers import assign_warehouse_role


class QuotationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='quotation-user')
        cls.root = get_user_model().objects.create_superuser(username='quotation-admin')
        cls.warehouse = WareHouse.objects.create(name='Main', address='Main address', phone='1')
        cls.payment_account = FinancialAccount.objects.create(name='Sales bank', type='bank', warehouse=cls.warehouse)
        cls.other_warehouse = WareHouse.objects.create(name='Branch', address='Branch address', phone='2')
        cls.role = assign_warehouse_role(cls.user, warehouse=cls.warehouse).role
        cls.party = Party.objects.create(code='QUOTE-CLIENT', name='Original Customer', address='Original address',
                                        is_customer=True)
        cls.unit = UnitOfMeasure.objects.create(code='QUOTE-PCS', name='Pieces')
        cls.product = Product.objects.create(sku='QUOTED', name='Original Product', unit=cls.unit, currency_id='BDT')
        cls.stock = Stock.objects.create(warehouse=cls.warehouse, product=cls.product, quantity=10)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.today = timezone.localdate()
        self.grant(*QUOTE_EDIT, *QUOTE_ADD, *QUOTE_DELETE, 'sale.view_salesorder', 'sale.change_salesorder',
                   *(permission for permissions in ACTION_PERMISSIONS.values() for permission in permissions))

    def grant(self, *permissions):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='sale',
            codename__in=[name.split('.')[1] for name in permissions]))

    def line(self, **changes):
        return {'line_number': 1, 'product': self.product.pk, 'unit': self.unit.pk, 'description': 'Quoted description',
                'quantity': '2.500000', 'unit_price': '100.0000', 'discount_amount': '10.0000', 'tax_rate': '5.0000',
                **changes}

    def create(self, expected=201, **changes):
        payload = {'customer': self.party.pk, 'exchange_rate': '1', 'currency': 'BDT', 'shipping_cost': '3',
                   'terms': 'Customer terms', 'notes': 'Internal note', 'items': [self.line()], **changes}
        response = self.client.post(reverse('sale:quotation-list'), payload, format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, expected, response.data)
        return response.data.get('sales_quotation', response.data)

    def action(self, pk, action, expected=200):
        data = {'payment_account': self.payment_account.pk} if action == 'convert' else {}
        response = self.client.post(reverse('sale:quotation-action', args=[pk, action]), data, format='json',
                                    HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, expected, response.data)
        return response.data.get('sales_quotation', response.data)

    def test_procurement_creates_one_draft_at_reviewed_supplier_prices(self):
        from purchase.models import PurchaseOrder

        record = self.create()
        self.action(record['id'], 'issue')
        quote = SalesQuotation.objects.get(pk=record['id'])
        vendor = Party.objects.create(code='QUOTE-VENDOR', name='Vendor', is_supplier=True)
        payload = {'supplier': vendor.pk, 'costs': [{'line': quote.items.get().pk, 'unit_cost': '16'}]}
        url = reverse('sale:quote-purchase', args=[quote.pk])
        self.assertEqual(self.client.post(url, payload, format='json', HTTP_X_CSRFTOKEN=self.token).status_code, 403)
        client = APIClient()
        client.force_login(self.root)
        result = client.post(url, payload, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(client.post(url, payload, format='json').status_code, 200)
        payload['costs'][0]['unit_cost'] = '17'
        self.assertEqual(client.post(url, payload, format='json').status_code, 409)
        purchase = PurchaseOrder.objects.get()
        self.assertEqual((purchase.grand_total, purchase.supplier_id), (Decimal('40'), vendor.pk))
        self.assertFalse(purchase.is_received)
        self.assertFalse(purchase.payments.exists())
        self.assertEqual(Stock.objects.get().quantity, Decimal('10'))
        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuotation.Status.ISSUED)
        self.assertEqual(quote.purchase_order_id, purchase.pk)

    def test_draft_crud_defaults_pricing_and_preserves_line_ids(self):
        record = self.create(status='issued')
        self.assertEqual(record['status'], 'draft')
        self.assertEqual(record['warehouse'], self.warehouse.pk)
        self.assertEqual(record['valid_until'], (self.today + timedelta(days=30)).isoformat())
        self.assertEqual((record['subtotal'], record['tax_total'], record['grand_total']), ('240.0000', '12.0000', '255.0000'))
        detail = reverse('sale:quotation-detail', args=[record['id']])
        item = self.client.get(detail).data['items'][0]
        data = {'items': [self.line(id=item['id'], unit_price='120.0000')]}
        response = self.client.patch(detail, data, format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['grand_total'], '307.5000')
        self.assertEqual(self.client.get(detail).data['items'][0]['id'], item['id'])
        self.create(expected=400, valid_until=(self.today - timedelta(days=1)).isoformat())
        self.create(expected=403, warehouse=self.other_warehouse.pk)
        self.create(expected=400, items=[self.line(), self.line()])
        self.assertEqual(self.client.delete(detail, HTTP_X_CSRFTOKEN=self.token).status_code, 204)
        self.assertFalse(SalesQuotation.objects.filter(pk=record['id']).exists())

    def test_issue_freezes_pdf_and_blocks_edits(self):
        record = self.create()
        pk = record['id']
        pdf_url = reverse('report:quotation-pdf', args=[pk])
        self.assertEqual(self.client.get(pdf_url).status_code, 404)
        self.assertEqual(self.action(pk, 'issue')['status'], 'issued')
        document = snapshot('quotation', pk, self.user)
        self.assertEqual(document['cards'][0]['name'], 'Original Customer')
        self.assertEqual(document['rows'][0]['name'], 'Original Product')
        self.assertEqual(document['notes'], 'Customer terms')
        Product.objects.filter(pk=self.product.pk).update(name='Changed product')
        Party.objects.filter(pk=self.party.pk).update(name='Changed customer', address='New address')
        self.assertEqual(snapshot('quotation', pk, self.user), document)
        response = self.client.get(pdf_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
        detail = reverse('sale:quotation-detail', args=[pk])
        self.assertEqual(self.client.get(detail).data['items'][0]['product_name'], 'Original Product')
        self.assertEqual(self.client.patch(detail, {'notes': 'Edit'}, format='json',
                                           HTTP_X_CSRFTOKEN=self.token).status_code, 409)
        self.assertEqual(self.client.delete(detail, HTTP_X_CSRFTOKEN=self.token).status_code, 409)
        self.client.force_login(self.root)
        admin_edit = reverse('admin:sale_salesquotation_change', args=[pk])
        self.assertEqual(self.client.get(admin_edit).status_code, 200)
        self.assertFalse(self.client.get(admin_edit).context['has_change_permission'])

    def test_accepted_quote_converts_once_and_stock_moves_on_order_completion(self):
        quote = self.create()
        pk = quote['id']
        self.action(pk, 'convert', expected=409)
        self.action(pk, 'issue')
        self.action(pk, 'accept')
        with patch('sale.services.timezone.localdate', return_value=self.today + timedelta(days=31)):
            self.assertEqual(self.action(pk, 'convert')['status'], 'converted')
            self.action(pk, 'convert')
        record = SalesQuotation.objects.get(pk=pk)
        order = record.sales_order
        self.assertEqual(SalesOrder.objects.count(), 1)
        self.assertEqual(order.status_id, 'draft')
        self.assertEqual(order.payment_account, self.payment_account)
        self.assertFalse(order.is_completed)
        self.assertEqual(order.grand_total, Decimal('255.0000'))
        self.assertEqual(order.items.get().quantity, Decimal('2.500000'))
        self.assertEqual(order.items.get().unit_price, Decimal('100.0000'))
        self.assertEqual(order.exchange_rate, Decimal('1'))
        self.assertEqual(order.notes, 'Customer terms')
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))
        detail = self.client.get(reverse('sale_pages:sale-detail', args=[order.pk]))
        self.assertContains(detail, 'Source quotation')
        response = self.client.post(reverse('sale:sales-order-complete'),
            {'sale_id': order.pk, 'is_completed': True}, format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 200, response.data)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('7.500000'))

    def test_expiry_decline_and_revision_preserve_original(self):
        expired = self.create(valid_until=self.today.isoformat())
        self.action(expired['id'], 'issue')
        with patch('sale.services.timezone.localdate', return_value=self.today + timedelta(days=1)):
            detail = self.client.get(reverse('sale:quotation-detail', args=[expired['id']]))
            self.assertEqual(detail.data['status_name'], 'Expired')
            self.action(expired['id'], 'accept', expected=409)
            revision = self.action(expired['id'], 'revise')
            self.assertEqual(self.action(expired['id'], 'revise')['id'], revision['id'])
        self.assertEqual(revision['status'], 'draft')
        self.assertEqual(revision['grand_total'], expired['grand_total'])
        self.assertEqual(SalesQuotation.objects.get(pk=expired['id']).status, 'superseded')
        next_quote = self.create()
        self.action(next_quote['id'], 'issue')
        self.action(next_quote['id'], 'decline')
        self.action(next_quote['id'], 'convert', expected=409)
        self.assertFalse(SalesOrder.objects.exists())

    def test_page_options_and_access_require_role_and_warehouse_scope(self):
        record = self.create()
        pk = record['id']
        self.assertContains(self.client.get(reverse('sale_pages:quotation-list')), 'Quotations')
        form = self.client.get(reverse('sale_pages:quotation-create'))
        self.assertContains(form, 'data-document-type="quotation"')
        self.assertContains(form, 'name="valid_until"')
        self.assertContains(form, 'name="terms"')
        detail = self.client.get(reverse('sale_pages:quotation-detail', args=[pk]))
        self.assertContains(detail, 'Issue quotation')
        options = self.client.get(reverse('sale:quotation-options')).data
        self.assertEqual([item['value'] for item in options['warehouse']], [self.warehouse.pk])
        foreign = SalesQuotation.objects.create(customer=self.party, currency_id='BDT', exchange_rate=1,
                                                 warehouse=self.other_warehouse)
        self.action(foreign.pk, 'issue', expected=404)
        self.action(pk, 'issue')
        self.role.permissions.clear()
        self.grant(*QUOTE_VIEW)
        response = self.client.get(reverse('sale:quotation-detail', args=[pk]))
        self.assertNotIn('items', response.data)
        self.assertEqual(self.client.get(reverse('report:quotation-pdf', args=[pk])).status_code, 403)
        self.action(pk, 'accept', expected=403)
