from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Currency, Product, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem
from warehouse.models import Stock


class PurchasePageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='purchase-page-operator')
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        self.product = Product.objects.create(name='Keyboard', sku='KEYBOARD', currency_id='BDT', unit=self.unit)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.list_url = reverse('purchase_pages:purchase-list')
        self.add_url = reverse('purchase_pages:create-purchase')
        self.api_url = reverse('purchase:purchase-order-list')
        self.options_url = reverse('purchase:purchase-options')

    def grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='purchase', codename__in=codenames,
        ))

    def grant_create(self):
        self.grant('view_purchaseorder', 'add_purchaseorder', 'add_purchaseorderitem')

    def payload(self):
        return {'supplier': None, 'currency': 'BDT', 'exchange_rate': '1', 'shipping_cost': '5',
                'items': [{'line_number': 1, 'product': self.product.pk, 'unit': self.unit.pk,
                           'quantity': '3', 'unit_price': '10', 'discount_amount': '2', 'tax_rate': '15'},
                          {'line_number': 2, 'product': self.product.pk, 'unit': self.unit.pk,
                           'quantity': '1', 'unit_price': '20'}]}

    def csrf(self):
        self.client.get(reverse('account:login'))
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)

    def test_pages_and_navigation_respect_all_required_permissions(self):
        for url in [self.list_url, self.add_url]:
            self.assertRedirects(Client().get(url), reverse('account_pages:login'))
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-purchase-toggle')
        self.grant('add_purchaseorder', 'add_purchaseorderitem')
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-purchase-toggle')
        self.assertEqual(self.client.get(self.add_url).status_code, 403)
        self.grant('view_purchaseorder')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'data-purchase-toggle')
        self.assertContains(response, f'href="{self.list_url}"')
        self.assertContains(response, f'href="{self.add_url}"')
        self.assertContains(self.client.get(self.list_url), 'aria-current="page">Purchase list')
        self.assertContains(self.client.get(self.add_url), 'aria-current="page">Create purchase')
        for url in [self.list_url, self.add_url]:
            response = self.client.get(url)
            self.assertContains(response, 'aria-controls="purchase-submenu" aria-expanded="true"')
            self.assertIn('no-store', response['Cache-Control'])

    def test_view_only_user_can_list_but_cannot_create(self):
        self.grant('view_purchaseorder')
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-api-url="{reverse("purchase:purchase-order-list-view")}"')
        self.assertContains(response, '/static/inventory/js/catalog_list.js')
        self.assertNotContains(response, f'href="{self.add_url}"')
        self.assertEqual(self.client.get(self.add_url).status_code, 403)
        self.grant('add_purchaseorder')
        self.assertEqual(self.client.get(self.add_url).status_code, 403)

    def test_create_page_is_api_driven_and_contains_header_and_item_controls(self):
        self.grant_create()
        response = self.client.get(self.add_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('form', response.context)
        self.assertContains(response, f'action="{self.api_url}"')
        self.assertContains(response, f'data-options-url="{self.options_url}"')
        self.assertContains(response, f'data-success-url="{self.list_url}"')
        self.assertContains(response, '/static/purchase/js/purchase_form.js')
        self.assertContains(response, 'data-purchase-submit disabled')
        self.assertContains(response, 'data-purchase-add-line disabled')
        self.assertContains(response, 'data-line-unit-label readonly')
        for name in ['supplier', 'order_date', 'expected_date', 'currency', 'exchange_rate', 'status', 'shipping_cost', 'notes']:
            self.assertContains(response, f'name="{name}"')
        for name in ['product', 'unit', 'quantity', 'unit_price', 'discount_amount', 'tax_rate', 'description']:
            self.assertContains(response, f'data-line-field="{name}"')

    def test_reference_data_needs_add_permissions_without_catalog_view_permissions(self):
        self.assertEqual(Client().get(self.options_url).status_code, 403)
        self.assertEqual(self.client.get(self.options_url).status_code, 403)
        self.grant('add_purchaseorder')
        self.assertEqual(self.client.get(self.options_url).status_code, 403)
        self.grant('add_purchaseorderitem')
        Currency.objects.create(name='US Dollar', code='USD', exchange_rate=None)
        response = self.client.get(self.options_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        data = response.data
        self.assertEqual(data['base_currency'], 'BDT')
        self.assertEqual(data['product'], [{'value': self.product.pk, 'label': 'KEYBOARD — Keyboard',
                                          'unit': self.unit.pk, 'unit_label': 'Pieces (PCS)'}])
        self.assertTrue(any(row['value'] == 'draft' for row in data['status']))
        self.assertIsNone(next(row for row in data['currency'] if row['value'] == 'USD')['exchange_rate'])
        self.csrf()
        self.assertEqual(self.client.post(self.options_url, {}, format='json').status_code, 405)

    def test_page_to_api_create_roundtrip_saves_items_totals_audit_and_list(self):
        self.grant_create()
        page = self.client.get(self.add_url)
        payload = self.payload()
        self.assertEqual(self.client.post(page.context['create_api_url'], payload, format='json').status_code, 403)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        response = self.client.post(page.context['create_api_url'], payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data['purchase_order']
        self.assertEqual(data['subtotal'], '48.0000')
        self.assertEqual(data['tax_total'], '4.2000')
        self.assertEqual(data['grand_total'], '57.2000')
        order = PurchaseOrder.objects.get(pk=data['id'])
        self.assertEqual(list(order.items.values_list('line_number', flat=True)), [1, 2])
        self.assertTrue(all(item.create_by == self.user.username and item.modified_by == self.user.username
                            for item in order.items.all()))
        self.assertFalse(Stock.objects.exists())
        listing = self.client.get(reverse('purchase:purchase-order-list-view'))
        self.assertEqual(listing.data['results'][0]['id'], order.pk)
        self.assertEqual(self.client.get(page.context['list_url']).status_code, 200)

    def test_nested_items_require_their_own_permission_and_header_only_still_works(self):
        self.grant('add_purchaseorder')
        self.csrf()
        response = self.client.post(self.api_url, self.payload(), format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PurchaseOrder.objects.exists())
        payload = self.payload()
        payload.pop('items')
        self.assertEqual(self.client.post(self.api_url, payload, format='json').status_code, 201)

    def test_invalid_items_and_headers_save_nothing_and_return_indexed_errors(self):
        self.grant_create()
        self.csrf()
        invalid_lines = [
            {'quantity': '0'}, {'quantity': '1.0000001'}, {'unit_price': '-1'},
            {'discount_amount': '100'}, {'tax_rate': '101'}, {'product': 99999},
            {'unit': UnitOfMeasure.objects.create(name='Kilograms', code='KG').pk}, {'line_number': 1},
        ]
        for change in invalid_lines:
            with self.subTest(change=change):
                payload = self.payload()
                payload['items'][1].update(change)
                response = self.client.post(self.api_url, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('items', response.data)
                self.assertFalse(PurchaseOrder.objects.exists())
                self.assertFalse(PurchaseOrderItem.objects.exists())
        for change in [{'items': []}, {'exchange_rate': '2'}]:
            response = self.client.post(self.api_url, {**self.payload(), **change}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertFalse(PurchaseOrder.objects.exists())

    def test_database_failure_on_second_line_rolls_back_order_and_first_line(self):
        self.grant_create()
        self.csrf()
        create_item = PurchaseOrderItem.objects.create
        calls = []

        def fail_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise IntegrityError('Simulated concurrent constraint failure')
            return create_item(**kwargs)

        with patch('purchase.serializers.PurchaseOrderItem.objects.create', side_effect=fail_second):
            response = self.client.post(self.api_url, self.payload(), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(calls), 2)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PurchaseOrderItem.objects.exists())
