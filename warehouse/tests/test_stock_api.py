from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from warehouse.models import Stock, WareHouse


class StockCreationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='stock-creator')
        self.warehouse = WareHouse.objects.create(name='Main depot', address='Dhaka', phone='123', code='MAIN')
        unit = UnitOfMeasure.objects.create(name='Kilograms', code='KG')
        self.product = Product.objects.create(name='Rice', sku='RICE', unit=unit, currency_id='BDT', cost_price='99')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.url = reverse('warehouse:stock-add')
        self.options = reverse('warehouse:stock-options')
        self.page = reverse('warehouse_pages:add-stock')
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)

    def grant(self, *permissions):
        for permission in permissions:
            app, codename = permission.split('.')
            self.user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))

    def payload(self, **changes):
        return {'warehouse': self.warehouse.pk, 'product': self.product.pk, 'quantity': '12.123456', **changes}

    def test_creation_requires_stock_permission_and_records_server_audit_fields(self):
        self.assertEqual(APIClient().post(self.url, self.payload(), format='json').status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.grant('warehouse.add_warehouse')
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.grant('warehouse.add_stock')
        response = self.client.post(self.url, self.payload(id=9999, create_by='spoof', modified_by='spoof'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['message'], 'Stock created successfully.')
        record = response.data['stock']
        self.assertEqual(record['quantity'], '12.123456')
        self.assertEqual(record['warehouse'], self.warehouse.pk)
        self.assertEqual(record['product'], self.product.pk)
        self.assertEqual(record['create_by'], self.user.username)
        self.assertEqual(record['modified_by'], self.user.username)
        self.assertNotEqual(record['id'], 9999)
        self.assertIn('no-store', response['Cache-Control'])

    def test_duplicate_pair_is_rejected_without_incrementing_or_overwriting_quantity(self):
        self.grant('warehouse.add_stock')
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 201)
        response = self.client.post(self.url, self.payload(quantity='5'), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Stock.objects.count(), 1)
        self.assertEqual(Stock.objects.get().quantity, Decimal('12.123456'))
        second = WareHouse.objects.create(name='Branch', address='Gazipur', phone='456')
        self.assertEqual(self.client.post(self.url, self.payload(warehouse=second.pk), format='json').status_code, 201)

    def test_validation_and_required_references(self):
        self.grant('warehouse.add_stock')
        for invalid in [{'quantity': '-1'}, {'quantity': '1.1234567'}, {'quantity': 'NaN'}, {'quantity': None},
                        {'warehouse': 9999}, {'product': 9999}, {'warehouse': None}, {'product': None}]:
            with self.subTest(data=invalid):
                self.assertEqual(self.client.post(self.url, self.payload(**invalid), format='json').status_code, 400)
        for field in ['warehouse', 'product']:
            data = self.payload()
            del data[field]
            self.assertEqual(self.client.post(self.url, data, format='json').status_code, 400)
        self.assertFalse(Stock.objects.exists())
        self.assertEqual(self.client.post(self.url, self.payload(quantity='0'), format='multipart').status_code, 201)

    def test_csrf_method_restrictions_and_database_race_handling(self):
        self.grant('warehouse.add_stock')
        self.client.credentials()
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)
        for method in ['get', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)
        self.assertEqual(self.client.options(self.url).status_code, 200)
        with patch('warehouse.serializers.StockSerializer.save', side_effect=IntegrityError):
            response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Stock.objects.exists())

    def test_options_return_only_identifiers_and_labels_without_other_management_grants(self):
        self.assertEqual(self.client.get(self.options).status_code, 403)
        self.grant('warehouse.add_stock')
        response = self.client.get(self.options)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            'warehouse': [{'value': self.warehouse.pk, 'label': 'Main depot (MAIN)'}],
            'product': [{'value': self.product.pk, 'label': 'RICE — Rice (KG)'}],
        })
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.get(reverse('warehouse:warehouse-list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('inventory:product-list-view')).status_code, 403)
        self.assertEqual(self.client.head(self.options).status_code, 200)
        for method in ['post', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(self.options).status_code, 405)

    def test_page_and_navigation_with_stock_only_permissions(self):
        self.assertRedirects(Client().get(self.page), reverse('account_pages:login'))
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), '>Add stock</a>')
        self.grant('warehouse.add_stock')
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'action="{self.url}"')
        self.assertContains(response, f'data-options-url="{self.options}"')
        self.assertContains(response, 'data-catalog-submit disabled')
        self.assertContains(response, f'data-success-url="{reverse("dashboard")}"')
        self.assertContains(response, 'step="0.000001"')
        self.assertNotContains(response, 'data-warehouses-toggle')
        self.assertNotIn('form', response.context)
        self.assertNotContains(response, '<option value="' + str(self.product.pk) + '">')
        self.assertNotContains(response, '>Warehouse list</a>')
        self.grant('warehouse.view_warehouse')
        response = self.client.get(self.page)
        self.assertContains(response, f'data-success-url="{reverse("warehouse_pages:warehouse-list")}"')
        self.assertNotContains(response, 'aria-current="page">Warehouse list')
        listing = self.client.get(reverse('warehouse_pages:warehouse-list'))
        self.assertContains(listing, 'data-stock-url-template="/warehouse/stocks/add/?warehouse=__warehouse__"')
        self.assertNotContains(listing, '>Add stock</a>')

    def test_row_action_permission_preselection_and_sidebar_removal(self):
        self.grant('warehouse.view_warehouse')
        url = reverse('warehouse_pages:warehouse-list')
        response = self.client.get(url)
        self.assertNotIn('stock_url_template', response.context)
        self.grant('warehouse.add_stock')
        response = self.client.get(url)
        self.assertEqual(response.context['columns'][-1]['key'], 'actions')
        self.assertNotIn('edit_url_template', response.context)
        self.assertNotIn('delete_url_template', response.context)
        nav = response.content.decode().split('<nav ', 1)[1].split('</nav>', 1)[0]
        self.assertNotIn(self.page, nav)
        for warehouse_id in [self.warehouse.pk, self.warehouse.pk + 100]:
            page = self.client.get(self.page, {'warehouse': warehouse_id})
            self.assertEqual(page.context['selected_warehouse'], str(warehouse_id))
            self.assertContains(page, f'data-default="{warehouse_id}"')
            self.assertContains(page, 'required disabled data-locked="true"')
        self.assertEqual(self.client.get(self.page, {'warehouse': '<script>'}).context['selected_warehouse'], '')

    def test_page_contract_supports_api_create_and_options_can_be_empty(self):
        self.grant('warehouse.add_stock')
        page = self.client.get(self.page)
        response = self.client.post(page.context['create_api_url'], self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.get(page.context['return_url']).status_code, 200)
        Stock.objects.all().delete()
        self.product.delete()
        self.warehouse.delete()
        response = self.client.get(self.options)
        self.assertEqual(response.data, {'warehouse': [], 'product': []})

    def test_group_grants_revocation_and_inactive_users(self):
        role = Group.objects.create(name='Stock creators')
        role.permissions.add(Permission.objects.get(content_type__app_label='warehouse', codename='add_stock'))
        self.user.groups.add(role)
        self.assertEqual(self.client.get(self.options).status_code, 200)
        self.assertEqual(self.client.get(self.page).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(self.options).status_code, 403)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.grant('warehouse.add_stock')
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
