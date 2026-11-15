from warehouse.tests.helpers import assign_warehouse_role
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from inventory.models import Product, UnitOfMeasure
from warehouse.models import Stock, RoleWarehouseAccess, WareHouse


class ManualStockRemovalTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='stock-admin', email='admin@example.com')
        self.client.force_login(self.admin)
        self.warehouse = WareHouse.objects.create(name='Main', address='Dhaka', phone='1')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        product = Product.objects.create(sku='STOCK', name='Product', unit=unit, currency_id='BDT')
        self.stock = Stock.objects.create(warehouse=self.warehouse, product=product, quantity=10)

    def test_manual_creation_and_options_urls_are_unavailable_even_to_superuser(self):
        response = self.client.post('/warehouse/api/stocks/', {
            'warehouse': self.warehouse.pk, 'product': self.stock.product_id, 'quantity': 999,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get('/warehouse/api/stocks/options/').status_code, 404)
        self.assertEqual(self.client.get(f'/warehouse/stocks/add/?warehouse={self.warehouse.pk}').status_code, 404)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))
        self.assertEqual(Stock.objects.count(), 1)

    def test_warehouse_list_has_no_add_stock_action_with_any_permission(self):
        url = reverse('warehouse_pages:warehouse-list')
        for actor in [self.admin, get_user_model().objects.create_user(username='legacy-stock-adder')]:
            access = assign_warehouse_role(actor, warehouse=self.warehouse)
            actor_role = access.role
            actor_role.permissions.set(Permission.objects.filter(content_type__app_label='warehouse',
                codename__in=['view_warehouse', 'add_stock']))
            self.client.force_login(actor)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'data-stock-url-template')
            self.assertNotContains(response, 'stocks/add/')
            self.assertNotIn('stock_url_template', response.context)

    def test_stock_list_and_detail_still_work_and_reject_writes(self):
        for route, args in [('warehouse:stock-list', []), ('warehouse:stock-detail', [self.stock.pk])]:
            url = reverse(route, args=args)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.post(url, {'quantity': 999}).status_code, 405)
