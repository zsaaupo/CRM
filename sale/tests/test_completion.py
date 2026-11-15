from warehouse.tests.helpers import assign_warehouse_role
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from common.models import TradeStatus
from inventory.models import Product, UnitOfMeasure
from sale.models import SalesOrder, SalesOrderItem
from warehouse.models import Stock, WareHouse


class SaleCompletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='sale-completer')
        self.role = assign_warehouse_role(self.user, all_warehouses=True).role
        self.grant('change_salesorder')
        self.warehouse = WareHouse.objects.create(name='Main', address='Dhaka', phone='1')
        self.branch = WareHouse.objects.create(name='Branch', address='Dhaka', phone='2')
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='COMPLETE-PCS')
        self.product = Product.objects.create(name='Rice', sku='COMPLETE-RICE', unit=self.unit, currency_id='BDT')
        self.other = Product.objects.create(name='Tea', sku='COMPLETE-TEA', unit=self.unit, currency_id='BDT')
        self.stock = Stock.objects.create(warehouse=self.warehouse, product=self.product, quantity='10', create_by='opening')
        self.order = self.new_order('3.123456')
        self.item = self.order.items.get()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)
        self.url = reverse('sale:sales-order-complete')

    def grant(self, *names):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='sale', codename__in=names))

    def new_order(self, quantity='2', warehouse=None):
        order = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=warehouse or self.warehouse,
                                          create_by='original', modified_by='original')
        SalesOrderItem.objects.create(sales_order=order, line_number=1, product=self.product, unit=self.unit,
                                       quantity=quantity, unit_price=10)
        return order

    def complete(self, order=None, expected=200, **changes):
        response = self.client.post(self.url, {'sale_id': (order or self.order).pk, 'is_completed': True, **changes}, format='json')
        self.assertEqual(response.status_code, expected, response.data)
        return response

    def test_complete_deducts_once_preserves_balance_id_audits_and_trade_status(self):
        status = TradeStatus.objects.create(code='review-complete', name='Review')
        SalesOrder.objects.filter(pk=self.order.pk).update(status=status)
        response = self.complete()
        self.assertTrue(response.data['completed_now'])
        self.assertTrue(response.data['sales_order']['is_completed'])
        self.assertIn('no-store', response['Cache-Control'])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('6.876544'))
        self.assertEqual(self.stock.modified_by, self.user.username)
        self.assertEqual(self.stock.create_by, 'opening')
        self.assertEqual(Stock.objects.count(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status_id, status.code)
        self.assertEqual(self.order.create_by, 'original')
        self.assertEqual(self.order.modified_by, self.user.username)
        self.assertIsNotNone(self.order.fulfilled_at)
        completed_at, updated_at = self.order.fulfilled_at, self.order.updated_at
        self.assertFalse(self.complete().data['completed_now'])
        self.order.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('6.876544'))
        self.assertEqual((self.order.fulfilled_at, self.order.updated_at), (completed_at, updated_at))

    def test_repeated_products_aggregate_and_only_selected_branch_is_deducted(self):
        Stock.objects.create(warehouse=self.branch, product=self.product, quantity=100)
        SalesOrderItem.objects.create(sales_order=self.order, line_number=2, product=self.product, unit=self.unit,
                                       quantity='1.000001', unit_price=1)
        self.complete()
        self.complete(self.new_order('2'))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('3.876543'))
        self.assertEqual(Stock.objects.get(warehouse=self.branch).quantity, Decimal('100'))
        self.assertEqual(Stock.objects.count(), 2)

    def test_shortage_on_later_line_rolls_back_all_balances_and_completion(self):
        SalesOrderItem.objects.create(sales_order=self.order, line_number=2, product=self.other, unit=self.unit,
                                       quantity=2, unit_price=1)
        Stock.objects.create(warehouse=self.warehouse, product=self.other, quantity=1)
        self.complete(expected=400)
        self.stock.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))
        self.assertFalse(self.order.is_completed)
        self.assertIsNone(self.order.fulfilled_at)
        self.assertEqual(self.order.modified_by, 'original')
        Stock.objects.filter(product=self.other).update(quantity=2)
        self.complete()
        self.assertEqual(Stock.objects.get(product=self.other).quantity, Decimal('0'))

    def test_duplicate_lines_cannot_each_spend_the_same_available_stock(self):
        SalesOrderItem.objects.create(sales_order=self.order, line_number=2, product=self.product, unit=self.unit,
                                       quantity=8, unit_price=1)
        self.complete(expected=400)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))

    def test_missing_balance_does_not_create_stock_or_take_from_other_warehouse(self):
        self.order.warehouse = self.branch
        self.order.save()
        self.complete(expected=400)
        self.assertFalse(Stock.objects.filter(warehouse=self.branch).exists())

    def test_missing_warehouse_inactive_warehouse_empty_order_and_unit_mismatch(self):
        self.complete(expected=404, sale_id=999999)
        self.order.warehouse = None
        self.order.save()
        self.complete(expected=400)
        self.order.warehouse = self.warehouse
        self.order.save()
        self.warehouse.is_active = False
        self.warehouse.save()
        self.complete(expected=400)
        self.warehouse.is_active = True
        self.warehouse.save()
        other_unit = UnitOfMeasure.objects.create(code='OTHER', name='Other')
        Product.objects.filter(pk=self.product.pk).update(unit=other_unit)
        self.complete(expected=400)
        self.item.delete()
        self.complete(expected=400)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_completed)

    def test_permissions_session_csrf_and_http_methods(self):
        payload = {'sale_id': self.order.pk, 'is_completed': True}
        self.assertEqual(APIClient().post(self.url, payload, format='json').status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 403)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)
        self.role.permissions.clear()
        self.complete(expected=403)
        group = Group.objects.create(name='Completers')
        group.permissions.add(Permission.objects.get(content_type__app_label='sale', codename='change_salesorder'))
        assign_warehouse_role(self.user, all_warehouses=True, role=group)
        for method in ['get', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(self.url, payload).status_code, 405)
        self.assertEqual(self.client.options(self.url).status_code, 200)
        self.complete()
        group.permissions.clear()
        self.complete(expected=403)

    def test_invalid_payloads(self):
        for payload in [{}, {'sale_id': self.order.pk}, {'is_completed': True},
                        {'sale_id': 0, 'is_completed': True}, {'sale_id': 2**63, 'is_completed': True},
                        {'sale_id': self.order.pk, 'is_completed': False}]:
            response = self.client.post(self.url, payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_completed_header_lines_and_nested_edits_are_read_only(self):
        self.grant('view_salesorder', 'view_salesorderitem', 'change_salesorderitem', 'delete_salesorder',
                   'delete_salesorderitem', 'add_salesorderitem')
        self.complete()
        for route, pk, put in [
            ('sales-order', self.order.pk, {'currency': 'BDT', 'exchange_rate': 1, 'is_completed': False}),
            ('sales-order-item', self.item.pk, {'sales_order': self.order.pk, 'line_number': 1,
                'product': self.product.pk, 'unit': self.unit.pk, 'quantity': 5, 'unit_price': 10}),
        ]:
            detail = reverse(f'sale:{route}-detail', args=[pk])
            update = reverse(f'sale:{route}-update', args=[pk])
            for method, url in [('patch', detail), ('put', detail), ('patch', update), ('put', update), ('delete', detail)]:
                response = getattr(self.client, method)(url, put, format='json')
                self.assertEqual(response.status_code, 409, response.data)
            self.assertEqual(self.client.get(detail).status_code, 200)
        payload = {'sales_order': self.order.pk, 'line_number': 2, 'product': self.other.pk,
                   'unit': self.unit.pk, 'quantity': 1, 'unit_price': 1}
        response = self.client.post(reverse('sale:sales-order-item-list'), payload, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        response = self.client.patch(reverse('sale:sales-order-update', args=[self.order.pk]), {
            'status': 'confirmed', 'items': [{'id': self.item.pk, **{key: value for key, value in payload.items() if key != 'sales_order'}}],
        }, format='json')
        self.assertEqual(response.status_code, 409, response.data)

    def test_regular_create_update_cannot_set_completion_flag(self):
        self.grant('add_salesorder')
        response = self.client.patch(reverse('sale:sales-order-update', args=[self.order.pk]),
            {'notes': 'Editable', 'is_completed': True, 'fulfilled_at': '2026-01-01T00:00:00Z'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['is_completed'])
        self.assertIsNone(response.data['fulfilled_at'])
        response = self.client.post(reverse('sale:sales-order-list'),
            {'currency': 'BDT', 'exchange_rate': 1, 'is_completed': True}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['sales_order']['is_completed'])

    def test_busy_returns_retryable_409_and_pages_wire_completion_permission(self):
        with patch('sale.services.lock_warehouse_scope', side_effect=OperationalError('database is locked')):
            response = self.complete(expected=409)
        self.assertEqual(response.data['detail'].code, 'sale_busy')
        self.grant('view_salesorder')
        listing = self.client.get(reverse('sale_pages:sale-list'))
        self.assertContains(listing, f'data-complete-url="{self.url}"')
        self.assertContains(listing, 'data-catalog-complete-form')
        self.role.permissions.clear()
        self.grant('view_salesorder')
        listing = self.client.get(reverse('sale_pages:sale-list'))
        self.assertNotContains(listing, 'data-catalog-complete-form')
