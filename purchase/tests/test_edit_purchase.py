from warehouse.tests.helpers import assign_warehouse_role
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import FinancialAccount
from inventory.models import Product, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseOrderItem
from purchase.page_views import PURCHASE_EDIT_PERMISSIONS
from warehouse.models import Stock, WareHouse


class PurchaseEditingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='purchase-editor')
        self.role = assign_warehouse_role(self.user, all_warehouses=True).role
        self.unit = UnitOfMeasure.objects.create(name='Pieces', code='EDIT-PCS')
        self.product = Product.objects.create(name='Rice', sku='EDIT-RICE', unit=self.unit, currency_id='BDT')
        self.warehouse = WareHouse.objects.create(name='Main', address='Dhaka', phone='1')
        self.payment_account = FinancialAccount.objects.create(name='Purchase bank', type='bank', opening_balance=100)
        self.order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate='1', warehouse=self.warehouse,
                                                  payment_account=self.payment_account, create_by='original',
                                                  modified_by='original')
        self.first = self.line(1, '2')
        self.second = self.line(2, '3')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.get(reverse('account:login')).data['csrfToken'])
        self.edit_page = reverse('purchase_pages:edit-purchase', args=[self.order.pk])
        self.update = reverse('purchase:purchase-order-update', args=[self.order.pk])
        self.detail = reverse('purchase:purchase-order-detail', args=[self.order.pk])

    def line(self, number, quantity):
        return PurchaseOrderItem.objects.create(purchase_order=self.order, line_number=number, product=self.product,
            unit=self.unit, quantity=quantity, unit_price='10', create_by='original', modified_by='original')

    def grant(self, *permissions):
        self.role.permissions.add(*Permission.objects.filter(
            content_type__app_label='purchase', codename__in=[p.split('.')[-1] for p in permissions]))

    def payload(self, item=None, **changes):
        data = {'line_number': item.line_number if item else 3, 'product': self.product.pk,
                'unit': self.unit.pk, 'quantity': str(item.quantity) if item else '1',
                'unit_price': '10', 'discount_amount': '0', 'tax_rate': '0', 'description': ''}
        if item:
            data['id'] = item.pk
        return {**data, **changes}

    def test_list_edit_action_and_page_require_the_editor_permissions(self):
        self.assertRedirects(Client().get(self.edit_page), reverse('account_pages:login'))
        self.assertEqual(self.client.get(self.edit_page).status_code, 403)
        self.grant('view_purchaseorder', 'change_purchaseorder')
        listing = self.client.get(reverse('purchase_pages:purchase-list'))
        self.assertNotIn('edit_url_template', listing.context)
        self.grant(*PURCHASE_EDIT_PERMISSIONS)
        listing = self.client.get(reverse('purchase_pages:purchase-list'))
        self.assertEqual(listing.context['edit_url_template'], '/purchase/purchase-orders/0/edit/')
        self.assertEqual(listing.context['edit_disabled_field'], 'is_received')
        self.assertContains(listing, 'data-edit-disabled-field="locked"')
        page = self.client.get(self.edit_page)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Edit purchase')
        self.assertContains(page, 'data-method="PATCH"')
        self.assertContains(page, f'action="{self.update}"')
        self.assertContains(page, f'data-detail-url="{self.detail}"')
        self.assertContains(page, f'data-items-url="/purchase/api/purchase-order-items/list/?purchase_order={self.order.pk}"')
        self.assertContains(page, 'data-options-url="/purchase/api/purchase-orders/edit-options/"')
        self.assertContains(page, 'data-success-url="/purchase/purchase-orders/"')
        self.assertContains(page, 'Save changes')
        self.assertContains(page, 'data-purchase-submit disabled')
        self.assertNotIn('form', page.context)
        self.assertIn('no-store', page['Cache-Control'])

    def test_editor_options_do_not_require_permission_to_create_orders(self):
        self.grant(*PURCHASE_EDIT_PERMISSIONS)
        response = self.client.get(reverse('purchase:purchase-edit-options'))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['warehouse'][0]['value'], self.warehouse.pk)
        self.assertEqual(self.client.get(reverse('purchase:purchase-options')).status_code, 403)

    def test_nested_edit_adds_changes_removes_lines_and_preserves_existing_ids_and_audits(self):
        self.grant(*PURCHASE_EDIT_PERMISSIONS)
        created_at = self.first.created_at
        response = self.client.patch(self.update, {'notes': 'Updated purchase', 'items': [
            self.payload(self.first, quantity='5.123456'), self.payload(line_number=2, quantity='4'),
        ]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual(self.order.notes, 'Updated purchase')
        self.assertEqual(self.order.create_by, 'original')
        self.assertEqual(self.first.quantity, Decimal('5.123456'))
        self.assertEqual(self.first.created_at, created_at)
        self.assertEqual(self.first.create_by, 'original')
        self.assertEqual(self.first.modified_by, self.user.username)
        self.assertFalse(PurchaseOrderItem.objects.filter(pk=self.second.pk).exists())
        added = self.order.items.exclude(pk=self.first.pk).get()
        self.assertEqual(added.create_by, self.user.username)
        self.assertFalse(Stock.objects.exists())
        page = self.client.get(self.edit_page)
        self.assertEqual(self.client.get(page.context['list_url']).status_code, 200)

    def test_swapping_line_numbers_keeps_both_ids_and_unchanged_lines_keep_modification_audit(self):
        self.grant(*PURCHASE_EDIT_PERMISSIONS)
        response = self.client.patch(self.detail, {'items': [
            self.payload(self.second, line_number=1), self.payload(self.first, line_number=2),
        ]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(list(self.order.items.values_list('pk', flat=True)), [self.second.pk, self.first.pk])
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        original_times = {item.pk: item.updated_at for item in [self.first, self.second]}
        response = self.client.patch(self.update, {'notes': 'Header only', 'items': [
            self.payload(self.second), self.payload(self.first),
        ]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for item in self.order.items.all():
            self.assertEqual(item.updated_at, original_times[item.pk])

    def test_child_permissions_are_checked_for_each_actual_nested_action(self):
        self.grant('change_purchaseorder')
        for items in [
            [self.payload(self.first)],
            [self.payload(self.first), self.payload(self.second), self.payload()],
            [self.payload(self.first, quantity='99'), self.payload(self.second)],
        ]:
            response = self.client.patch(self.update, {'notes': 'Denied', 'items': items}, format='json')
            self.assertEqual(response.status_code, 403, response.data)
            self.order.refresh_from_db()
            self.assertEqual(self.order.notes, '')
        response = self.client.patch(self.update, {'notes': 'Allowed', 'items': [
            self.payload(self.first), self.payload(self.second),
        ]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_wrong_duplicate_or_create_time_item_ids_and_duplicate_line_numbers_are_rejected(self):
        self.grant(*PURCHASE_EDIT_PERMISSIONS, 'add_purchaseorder')
        other_order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=1)
        foreign_line = PurchaseOrderItem.objects.create(purchase_order=other_order, line_number=1,
            product=self.product, unit=self.unit, quantity=1, unit_price=1)
        for items in [[self.payload(self.first, id=foreign_line.pk)],
                      [self.payload(self.first), self.payload(self.first, line_number=2)],
                      [self.payload(self.first), self.payload(self.second, line_number=1)], []]:
            response = self.client.patch(self.update, {'items': items}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(reverse('purchase:purchase-order-list'), {
            'currency': 'BDT', 'exchange_rate': '1', 'payment_account': self.payment_account.pk,
            'items': [self.payload(self.first)],
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.order.items.count(), 2)

    def test_failure_after_deletion_rolls_back_header_and_every_line(self):
        self.grant(*PURCHASE_EDIT_PERMISSIONS)
        with patch('purchase.serializers.PurchaseOrderItem.objects.create', side_effect=RuntimeError('Save failed')):
            with self.assertRaises(RuntimeError):
                self.client.patch(self.update, {'notes': 'Should roll back', 'items': [
                    self.payload(self.first, quantity='7'), self.payload(line_number=2),
                ]}, format='json')
        self.order.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual(self.order.notes, '')
        self.assertEqual(self.first.quantity, Decimal('2'))
        self.assertTrue(PurchaseOrderItem.objects.filter(pk=self.second.pk).exists())

    def test_received_purchase_stays_readable_but_nested_edit_is_rejected(self):
        self.grant(*PURCHASE_EDIT_PERMISSIONS)
        response = self.client.post(reverse('purchase:purchase-order-receive'), {
            'purchase_id': self.order.pk, 'is_received': True,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch(self.update, {'notes': 'Locked', 'items': [self.payload(self.first)]}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        data = self.client.get(self.detail).data
        self.assertTrue(data['is_received'])
        self.assertEqual(self.order.items.count(), 2)

    def test_more_than_one_page_of_items_can_be_loaded_before_editing_and_csrf_is_required(self):
        self.grant(*PURCHASE_EDIT_PERMISSIONS)
        for number in range(3, 27):
            self.line(number, '1')
        page = self.client.get(self.edit_page)
        data = self.client.get(page.context['items_api_url']).data
        self.assertEqual(data['count'], 26)
        self.assertEqual(len(data['results']), 20)
        self.assertEqual(len(self.client.get(data['next']).data['results']), 6)
        self.client.credentials()
        response = self.client.patch(self.update, {'notes': 'Denied'}, format='json')
        self.assertEqual(response.status_code, 403)
