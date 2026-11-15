from warehouse.tests.helpers import assign_warehouse_role
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from inventory.models import Product, UnitOfMeasure
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem
from warehouse.models import Stock, WareHouse


class PurchaseReceivingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='purchase-receiver')
        self.role = assign_warehouse_role(self.user, all_warehouses=True).role
        self.role.permissions.add(Permission.objects.get(content_type__app_label='purchase', codename='change_purchaseorder'))
        self.warehouse = WareHouse.objects.create(name='Main', address='Dhaka', phone='1')
        self.branch = WareHouse.objects.create(name='Branch', address='Dhaka', phone='2')
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='RECEIVE-PCS')
        self.product = Product.objects.create(name='Rice', sku='RECEIVE-RICE', unit=self.unit, currency_id='BDT')
        self.other = Product.objects.create(name='Tea', sku='RECEIVE-TEA', unit=self.unit, currency_id='BDT')
        self.order = self.order_with_item('3.123456')
        self.item = self.order.items.get()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)
        self.url = reverse('purchase:purchase-order-receive')

    def order_with_item(self, quantity='2', warehouse=None, supplier=None):
        order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=warehouse or self.warehouse,
                                              supplier=supplier, create_by='original', modified_by='original')
        PurchaseOrderItem.objects.create(purchase_order=order, line_number=1, product=self.product, unit=self.unit,
                                         quantity=quantity, unit_price='10')
        return order

    def receive(self, order=None, status=200, **changes):
        data = {'purchase_id': (order or self.order).pk, 'is_received': True, **changes}
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status, response.data)
        return response

    def grant(self, *codenames):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='purchase', codename__in=codenames))

    def test_receiving_adds_stock_and_marks_purchase_read_only(self):
        response = self.receive()
        self.assertTrue(response.data['received_now'])
        self.assertTrue(response.data['purchase_order']['is_received'])
        self.assertEqual(response.data['purchase_order']['warehouse'], self.warehouse.pk)
        self.assertIn('no-store', response['Cache-Control'])
        stock = Stock.objects.get(warehouse=self.warehouse, product=self.product)
        self.assertEqual(stock.quantity, Decimal('3.123456'))
        self.assertEqual(stock.create_by, self.user.username)
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_received)
        self.assertEqual(self.order.modified_by, self.user.username)
        self.assertEqual(self.order.create_by, 'original')
        self.assertEqual(self.order.status_id, 'draft')  # Receiving is independent of trade status.

    def test_retries_do_not_add_twice_or_change_receipt_audit(self):
        self.receive()
        self.order.refresh_from_db()
        received_at = self.order.updated_at
        self.assertFalse(self.receive().data['received_now'])
        self.order.refresh_from_db()
        self.assertEqual(self.order.updated_at, received_at)
        self.assertEqual(Stock.objects.get().quantity, Decimal('3.123456'))

    def test_repeated_products_purchases_suppliers_and_branches_use_existing_balance(self):
        Stock.objects.create(warehouse=self.warehouse, product=self.product, quantity='10', create_by='opening')
        stock_id = Stock.objects.get().pk
        PurchaseOrderItem.objects.create(purchase_order=self.order, line_number=2, product=self.product, unit=self.unit,
                                         quantity='1.000001', unit_price=1)
        self.receive()
        supplier = Party.objects.create(code='RECEIVE-SUP', name='Other supplier', is_supplier=True)
        self.receive(self.order_with_item('2', supplier=supplier))
        self.receive(self.order_with_item('7', warehouse=self.branch))
        self.assertEqual(Stock.objects.get(warehouse=self.warehouse).pk, stock_id)
        self.assertEqual(Stock.objects.get(warehouse=self.warehouse).quantity, Decimal('16.123457'))
        self.assertEqual(Stock.objects.get(warehouse=self.branch).quantity, Decimal('7'))
        self.assertEqual(Stock.objects.count(), 2)

    def test_received_header_and_items_cannot_be_changed_deleted_or_extended(self):
        self.grant('change_purchaseorderitem', 'delete_purchaseorder', 'delete_purchaseorderitem',
                   'add_purchaseorderitem', 'view_purchaseorder', 'view_purchaseorderitem')
        self.receive()
        for route, pk, changes in [
            ('purchase-order', self.order.pk, {'notes': 'Changed', 'is_received': False, 'warehouse': self.branch.pk}),
            ('purchase-order-item', self.item.pk, {'quantity': '99'}),
        ]:
            detail = reverse(f'purchase:{route}-detail', args=[pk])
            update = reverse(f'purchase:{route}-update', args=[pk])
            for method, url in [('patch', detail), ('put', detail), ('patch', update), ('put', update), ('delete', detail)]:
                payload = changes if method != 'put' else ({
                    'currency': 'BDT', 'exchange_rate': '1', 'warehouse': self.branch.pk,
                } if route == 'purchase-order' else {
                    'purchase_order': self.order.pk, 'product': self.product.pk, 'unit': self.unit.pk,
                    'line_number': 1, 'quantity': '5', 'unit_price': '10',
                })
                response = getattr(self.client, method)(url, payload, format='json')
                self.assertEqual(response.status_code, 409, response.data)
            self.assertEqual(self.client.get(detail).status_code, 200)
        new_line = {'purchase_order': self.order.pk, 'line_number': 2, 'product': self.other.pk,
                    'unit': self.unit.pk, 'quantity': '2', 'unit_price': '10'}
        response = self.client.post(reverse('purchase:purchase-order-item-list'), new_line, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_received)
        self.assertEqual(self.order.warehouse_id, self.warehouse.pk)
        self.assertEqual(self.order.items.count(), 1)

    def test_items_cannot_move_into_or_out_of_a_received_purchase(self):
        self.grant('change_purchaseorderitem')
        other = self.order_with_item()
        self.receive()
        received_line = reverse('purchase:purchase-order-item-update', args=[self.item.pk])
        ordinary_line = reverse('purchase:purchase-order-item-update', args=[other.items.get().pk])
        for url, destination in [(received_line, other.pk), (ordinary_line, self.order.pk)]:
            response = self.client.patch(url, {'purchase_order': destination, 'line_number': 5}, format='json')
            self.assertEqual(response.status_code, 409, response.data)

    def test_unreceived_purchase_and_lines_remain_editable_and_flag_cannot_bypass_receiving(self):
        self.grant('add_purchaseorder', 'change_purchaseorderitem')
        detail = reverse('purchase:purchase-order-update', args=[self.order.pk])
        response = self.client.patch(detail, {'notes': 'Editable', 'is_received': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['is_received'])
        self.assertFalse(Stock.objects.exists())
        response = self.client.post(reverse('purchase:purchase-order-list'), {
            'currency': 'BDT', 'exchange_rate': '1', 'is_received': True, 'warehouse': self.branch.pk,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['purchase_order']['is_received'])
        response = self.client.patch(reverse('purchase:purchase-order-item-update', args=[self.item.pk]),
                                     {'quantity': '4'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_missing_empty_purchase_and_receiving_warehouse_errors_do_not_lock_order(self):
        self.receive(status=404, purchase_id=99999)
        self.order.warehouse = None
        self.order.save()
        self.receive(status=400)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_received)
        self.order.warehouse = self.warehouse
        self.order.save()
        self.warehouse.is_active = False
        self.warehouse.save()
        self.receive(status=400)
        self.warehouse.is_active = True
        self.warehouse.save()
        self.item.delete()
        self.receive(status=400)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_received)
        self.assertFalse(Stock.objects.exists())

    def test_false_and_invalid_payloads_are_rejected(self):
        for payload in [{}, {'purchase_id': self.order.pk}, {'is_received': True},
                        {'purchase_id': self.order.pk, 'is_received': False},
                        {'purchase_id': self.order.pk, 'is_received': None},
                        {'purchase_id': self.order.pk, 'is_received': 'invalid'},
                        {'purchase_id': -1, 'is_received': True},
                        {'purchase_id': 10 ** 30, 'is_received': True}]:
            response = self.client.post(self.url, payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Stock.objects.exists())

    def test_all_stock_updates_and_receipt_flag_roll_back_on_failure(self):
        PurchaseOrderItem.objects.create(purchase_order=self.order, line_number=2, product=self.other, unit=self.unit,
                                         quantity='5', unit_price=1)
        Stock.objects.create(warehouse=self.warehouse, product=self.other, quantity=Decimal('999999999998.000000'))
        self.receive(status=400)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_received)
        self.assertFalse(Stock.objects.filter(product=self.product).exists())
        self.assertEqual(self.order.modified_by, 'original')
        with patch('purchase.services.Stock.objects.get_or_create', side_effect=RuntimeError('Stock write failed')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, {'purchase_id': self.order.pk, 'is_received': True}, format='json')
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_received)

    def test_unit_mismatch_rolls_back_and_database_locks_return_retryable_conflict(self):
        new_unit = UnitOfMeasure.objects.create(name='Other', code='RECEIVE-OTHER')
        self.product.unit = new_unit
        self.product.save()
        self.receive(status=400)
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_received)
        with patch('purchase.services.lock_warehouse_scope', side_effect=OperationalError('database is locked')):
            self.receive(status=409)
        self.grant('change_purchaseorderitem')
        with patch('purchase.serializers.PurchaseOrderItemSerializer.is_valid', side_effect=OperationalError('database table is locked')):
            response = self.client.patch(reverse('purchase:purchase-order-item-update', args=[self.item.pk]),
                                         {'quantity': '4'}, format='json')
            self.assertEqual(response.status_code, 409, response.data)

    def test_permission_csrf_group_grants_and_supported_methods(self):
        self.role.permissions.clear()
        self.receive(status=403)
        role = Group.objects.create(name='Purchase receivers')
        role.permissions.add(Permission.objects.get(content_type__app_label='purchase', codename='change_purchaseorder'))
        assign_warehouse_role(self.user, all_warehouses=True, role=role)
        self.client.credentials()
        self.receive(status=403)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(self.client.options(self.url).status_code, 200)
        for method in ['get', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)
        self.receive()
        role.permissions.clear()
        self.receive(status=403)

    def test_purchase_list_exposes_receive_action_only_with_change_permission(self):
        view = Permission.objects.get(content_type__app_label='purchase', codename='view_purchaseorder')
        change = Permission.objects.get(content_type__app_label='purchase', codename='change_purchaseorder')
        self.role.permissions.clear()
        self.role.permissions.add(view)
        url = reverse('purchase_pages:purchase-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('receive_api_url', response.context)
        self.assertNotContains(response, 'data-catalog-receive-form')
        self.role.permissions.add(change)
        response = self.client.get(url)
        self.assertEqual(response.context['receive_api_url'], self.url)
        self.assertContains(response, f'data-receive-url="{self.url}"')
        self.assertContains(response, 'data-catalog-receive-form')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertNotIn('edit_url_template', response.context)

    def test_list_action_api_contract_returns_received_flag_and_safe_retries(self):
        self.role.permissions.add(Permission.objects.get(content_type__app_label='purchase', codename='view_purchaseorder'))
        page = self.client.get(reverse('purchase_pages:purchase-list'))
        response = self.client.post(page.context['receive_api_url'], {'purchase_id': self.order.pk, 'is_received': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['purchase_order']['is_received'])
        listing = self.client.get(page.context['list_api_url']).data
        self.assertTrue(next(record for record in listing['results'] if record['id'] == self.order.pk)['is_received'])
        self.assertFalse(self.receive().data['received_now'])
        self.assertEqual(Stock.objects.get().quantity, Decimal('3.123456'))

    def test_list_delete_action_requires_both_delete_permissions_and_disables_received_rows(self):
        self.grant('view_purchaseorder')
        url = reverse('purchase_pages:purchase-list')
        self.assertNotContains(self.client.get(url), 'data-catalog-delete-form')
        self.grant('delete_purchaseorder')
        self.assertNotContains(self.client.get(url), 'data-catalog-delete-form')
        self.grant('delete_purchaseorderitem')
        response = self.client.get(url)
        self.assertContains(response, 'data-catalog-delete-form')
        self.assertContains(response, 'data-delete-url-template="/purchase/api/purchase-orders/0/"')
        self.assertContains(response, 'data-delete-disabled-field="is_received"')
