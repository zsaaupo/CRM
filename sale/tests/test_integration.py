from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from people.models import Party
from sale.models import SalesOrder, SalesOrderItem
from warehouse.models import Stock, WareHouse


class SalesIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='sales-editor')
        self.customer = Party.objects.create(code='CUST', name='Customer', is_customer=True, address='Original address')
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        self.product = Product.objects.create(name='Keyboard', sku='KEY', unit=self.unit, currency_id='BDT',
                                               sale_price='10', product_tax='15')
        self.warehouse = WareHouse.objects.create(name='Main', address='Dhaka', phone='01')
        self.stock = Stock.objects.create(product=self.product, warehouse=self.warehouse, quantity='20')
        self.order = SalesOrder.objects.create(customer=self.customer, warehouse=self.warehouse,
            currency_id='BDT', exchange_rate='1', shipping_cost='5', billing_address='Saved billing',
            shipping_address='Saved shipping', create_by='original', modified_by='original')
        self.first = SalesOrderItem.objects.create(sales_order=self.order, line_number=1, product=self.product,
            unit=self.unit, quantity='3', unit_price='10', discount_amount='2', tax_rate='15',
            create_by='original', modified_by='original')
        self.second = SalesOrderItem.objects.create(sales_order=self.order, line_number=2, product=self.product,
            unit=self.unit, quantity='1', unit_price='20', create_by='original', modified_by='original')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        self.grant_all()
        self.url = reverse('sale:sales-order-list')
        self.detail = reverse('sale:sales-order-detail', args=[self.order.pk])
        self.update = reverse('sale:sales-order-update', args=[self.order.pk])

    def grant_all(self):
        self.user.user_permissions.set(Permission.objects.filter(content_type__app_label='sale'))

    def grant_only(self, *codenames):
        self.user.user_permissions.set(Permission.objects.filter(content_type__app_label='sale', codename__in=codenames))

    def line(self, number=1, **changes):
        return {'line_number': number, 'product': self.product.pk, 'unit': self.unit.pk,
                'quantity': '3', 'unit_price': '10', 'discount_amount': '2', 'tax_rate': '15', **changes}

    def payload(self, **changes):
        return {'customer': self.customer.pk, 'warehouse': self.warehouse.pk, 'currency': 'BDT',
                'exchange_rate': '1', 'billing_address': 'Billing snapshot', 'shipping_address': 'Shipping snapshot',
                'shipping_cost': '5', 'items': [self.line()], **changes}

    def test_create_order_and_items_is_atomic_and_never_changes_stock(self):
        response = self.client.post(self.url, self.payload(status='confirmed', confirmed_at='2026-01-01T00:00:00Z'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data['sales_order']
        self.assertTrue(data['order_number'].startswith('SO-'))
        self.assertEqual(data['status'], 'draft')
        self.assertIsNone(data['confirmed_at'])
        self.assertEqual(data['grand_total'], '37.2000')
        order = SalesOrder.objects.get(pk=data['id'])
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.get().create_by, self.user.username)
        self.customer.address = 'New address'
        self.customer.save()
        order.refresh_from_db()
        self.assertEqual(order.billing_address, 'Billing snapshot')
        self.assertEqual(order.shipping_address, 'Shipping snapshot')
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('20'))

    def test_nested_update_swaps_lines_keeps_ids_audit_and_recalculates_totals(self):
        payload = {'items': [self.line(1, id=self.second.pk, unit_price='20', quantity='1', discount_amount='0', tax_rate='0'),
                             self.line(2, id=self.first.pk, quantity='4')]}
        response = self.client.patch(self.update, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['grand_total'], '68.7000')
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.line_number, 2)
        self.assertEqual(self.second.line_number, 1)
        self.assertEqual(self.first.create_by, 'original')
        self.assertEqual(self.first.modified_by, self.user.username)
        self.assertEqual(self.first.quantity, Decimal('4'))

    def test_nested_removals_and_insertions_require_their_own_permissions(self):
        self.grant_only('change_salesorder')
        response = self.client.patch(self.update, {'items': [self.line(1, id=self.first.pk)]}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.order.items.count(), 2)
        self.grant_only('change_salesorder', 'delete_salesorderitem')
        response = self.client.patch(self.update, {'items': [self.line(1, id=self.first.pk)]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.order.items.count(), 1)
        response = self.client.patch(self.update, {'items': [self.line(1, id=self.first.pk, quantity='4')]}, format='json')
        self.assertEqual(response.status_code, 403)
        self.grant_only('change_salesorder', 'change_salesorderitem', 'add_salesorderitem')
        response = self.client.patch(self.update, {'items': [self.line(1, id=self.first.pk, quantity='4'), self.line(2)]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.order.items.count(), 2)

    def test_invalid_or_foreign_item_ids_and_duplicate_numbers_leave_order_unchanged(self):
        other = SalesOrder.objects.create(currency_id='BDT', exchange_rate='1')
        foreign = SalesOrderItem.objects.create(sales_order=other, line_number=1, product=self.product,
                                               unit=self.unit, quantity='1', unit_price='1')
        cases = [[self.line(1, id=foreign.pk)], [self.line(1, id=self.first.pk), self.line(2, id=self.first.pk)],
                 [self.line(1), self.line(1)], [], [self.line(1, quantity='0')]]
        for items in cases:
            response = self.client.patch(self.update, {'notes': 'Must roll back', 'items': items}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.order.refresh_from_db()
            self.assertEqual(self.order.notes, '')
            self.assertEqual(self.order.items.count(), 2)

    def test_database_failure_rolls_back_nested_edits_and_nested_creation(self):
        with patch('sale.serializers.SalesOrderItem.objects.create', side_effect=IntegrityError):
            response = self.client.patch(self.update, {'notes': 'Failed', 'items': [self.line()]}, format='json')
            self.assertEqual(response.status_code, 400)
            response = self.client.post(self.url, self.payload(), format='json')
            self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(SalesOrder.objects.count(), 1)
        self.assertEqual(self.order.items.count(), 2)
        self.assertEqual(self.order.notes, '')

    def test_customer_options_and_validation_preserve_deleted_customer_history(self):
        supplier = Party.objects.create(code='SUP', name='Supplier only', is_supplier=True)
        both = Party.objects.create(code='BOTH', name='Both', is_customer=True, is_supplier=True)
        deleted = Party.objects.create(code='DEL', name='Deleted customer', is_customer=True)
        deleted.delete()
        self.grant_only('add_salesorder', 'add_salesorderitem', 'view_salesorder', 'change_salesorder')
        options = self.client.get(reverse('sale:sale-options')).data
        self.assertEqual({item['value'] for item in options['customer']}, {self.customer.pk, both.pk})
        self.assertEqual(options['product'][0]['sale_price'], '10.0000')
        self.assertEqual(self.client.get(reverse('people:party-list')).status_code, 403)
        for invalid in [supplier.pk, deleted.pk, 999999]:
            response = self.client.post(self.url, self.payload(customer=invalid), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.customer.delete()
        self.assertEqual(self.client.get(self.detail).data['customer_name'], 'Customer')
        self.assertEqual(self.client.patch(self.update, {'notes': 'History'}, format='json').status_code, 200)

    def test_non_draft_order_and_its_items_reject_all_mutations(self):
        SalesOrder.objects.filter(pk=self.order.pk).update(status_id='confirmed')
        for method, url, data in [
            ('patch', self.update, {'notes': 'Forbidden'}), ('delete', self.detail, {}),
            ('post', reverse('sale:sales-order-item-list'), {**self.line(3), 'sales_order': self.order.pk}),
            ('patch', reverse('sale:sales-order-item-update', args=[self.first.pk]), {'quantity': '4'}),
            ('delete', reverse('sale:sales-order-item-detail', args=[self.first.pk]), {}),
        ]:
            self.assertEqual(getattr(self.client, method)(url, data, format='json').status_code, 409)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('20'))

    def test_delete_draft_with_items_needs_item_delete_permission_and_keeps_stock(self):
        self.grant_only('delete_salesorder')
        self.assertEqual(self.client.delete(self.detail).status_code, 403)
        self.assertEqual(self.order.items.count(), 2)
        self.grant_only('delete_salesorder', 'delete_salesorderitem')
        self.assertEqual(self.client.delete(self.detail).status_code, 204)
        self.assertFalse(SalesOrder.objects.filter(pk=self.order.pk).exists())
        self.assertFalse(SalesOrderItem.objects.filter(sales_order_id=self.order.pk).exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('20'))

    def test_sales_pages_navigation_and_api_contracts(self):
        urls = [reverse('sale_pages:sale-list'), reverse('sale_pages:create-sale'),
                reverse('sale_pages:sale-detail', args=[self.order.pk]), reverse('sale_pages:edit-sale', args=[self.order.pk])]
        for url in urls:
            self.assertRedirects(Client().get(url), reverse('account_pages:login'))
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertIn('no-store', page['Cache-Control'])
            self.assertContains(page, 'data-sales-toggle')
        listing = self.client.get(urls[0])
        self.assertContains(listing, 'data-edit-url-template="/sale/sales-orders/0/edit/"')
        self.assertContains(listing, 'data-delete-url-template="/sale/api/sales-orders/0/"')
        editing = self.client.get(urls[3])
        self.assertContains(editing, f'data-items-url="/sale/api/sales-order-items/list/?sales_order={self.order.pk}"')
        self.assertContains(editing, 'data-method="PATCH"')
        self.assertContains(editing, 'name="billing_address"')
        self.grant_only('view_salesorder')
        self.assertEqual(self.client.get(urls[0]).status_code, 200)
        self.assertEqual(self.client.get(urls[2]).status_code, 200)
        self.assertNotContains(self.client.get(urls[2]), 'data-sale-items')
        self.assertEqual(self.client.get(urls[1]).status_code, 403)
        self.assertEqual(self.client.get(urls[3]).status_code, 403)
        self.grant_only()
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-sales-toggle')
