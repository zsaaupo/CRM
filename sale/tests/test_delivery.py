from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounting.models import MAX_AMOUNT, AccountTransaction, ChalanPayment, FinancialAccount
from audit_log.models import AuditLog
from inventory.models import Currency, Product, UnitOfMeasure
from people.models import Party
from sale.models import Chalan, ChalanItem, Courier, Delivery, DeliveryStatus, PackingSlip
from warehouse.models import Stock, WareHouse
from warehouse.tests.helpers import assign_warehouse_role


def grant(role, *codenames):
    role.permissions.add(*Permission.objects.filter(content_type__app_label='sale', codename__in=codenames))


def session_client(user):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    token = client.get(reverse('account:login')).data['csrfToken']
    client.credentials(HTTP_X_CSRFTOKEN=token)
    return client


class CourierTests(TestCase):
    def test_create_edit_and_search_a_courier_with_role_permissions(self):
        user = get_user_model().objects.create_user(username='courier-admin')
        role = Group.objects.create(name='Courier directory role')
        user.groups.add(role)
        client = session_client(user)
        list_url = reverse('sale:courier-list')
        page = reverse('sale_pages:courier-list')

        self.assertEqual(client.get(page).status_code, 403)
        self.assertEqual(client.post(list_url, {'name': 'Same-day Courier'}, format='json').status_code, 403)
        grant(role, 'view_courier', 'add_courier', 'change_courier')
        self.assertContains(client.get(page), 'Create courier')
        self.assertContains(client.get(reverse('sale_pages:courier-create')), 'Courier name')
        response = client.post(list_url, {'name': '  Same-day Courier  ', 'email': 'courier@example.com'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        courier = response.data['courier']
        self.assertEqual(courier['name'], 'Same-day Courier')
        self.assertTrue(courier['is_active'])
        self.assertEqual(client.post(list_url, {'name': 'same-day courier'}, format='json').status_code, 400)
        self.assertEqual(client.get(list_url, {'q': 'same-day'}).data['count'], 1)
        self.assertContains(client.get(reverse('sale_pages:courier-edit', args=[courier['id']])), 'Edit courier')
        response = client.patch(reverse('sale:courier-detail', args=[courier['id']]), {'is_active': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['is_active'])


class DeliveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.warehouse = WareHouse.objects.create(name='Delivery warehouse')
        cls.other_warehouse = WareHouse.objects.create(name='Other delivery warehouse')
        cls.customer = Party.objects.create(code='DEL-CUSTOMER', name='Delivery customer', is_customer=True)
        cls.unit = UnitOfMeasure.objects.create(name='Piece', code='DEL-PCS')
        cls.product = Product.objects.create(sku='DEL-ITEM', name='Delivery product', currency_id='BDT', unit=cls.unit)
        cls.chalan = Chalan.objects.create(customer=cls.customer, warehouse=cls.warehouse, shipping_address='Ship here')
        cls.item = ChalanItem.objects.create(chalan=cls.chalan, line_number=1, product=cls.product, unit=cls.unit,
                                            quantity='1.500001')
        cls.stock = Stock.objects.create(warehouse=cls.warehouse, product=cls.product, quantity=10)
        cls.courier = Courier.objects.create(name='Primary courier')
        cls.other_courier = Courier.objects.create(name='Alternate courier')
        cls.user = get_user_model().objects.create_user(username='delivery-operator')
        cls.role = assign_warehouse_role(cls.user, warehouse=cls.warehouse).role

    def setUp(self):
        self.client = session_client(self.user)

    def assignment(self, chalan=None, **changes):
        return self.client.post(reverse('sale:delivery-assign', args=[(chalan or self.chalan).pk]),
                                {'courier': self.courier.pk, 'tracking_number': 'TRACK-105', **changes}, format='json')

    def transition(self, delivery_id, status, **changes):
        return self.client.post(reverse('sale:delivery-status', args=[delivery_id]),
                                {'status': status, **changes}, format='json')

    def test_assign_handover_and_track_status_with_related_packing_slip(self):
        grant(self.role, 'view_chalan', 'view_chalanitem', 'view_packingslip', 'add_packingslip',
              'view_delivery', 'add_delivery', 'change_delivery')
        chalan_page = reverse('sale_pages:chalan-detail', args=[self.chalan.pk])
        assignment_page = reverse('sale_pages:delivery-assign', args=[self.chalan.pk])
        self.assertContains(self.client.get(chalan_page), 'Assign courier')
        self.assertContains(self.client.get(assignment_page), self.courier.name)

        response = self.assignment()
        self.assertEqual(response.status_code, 201, response.data)
        delivery_id = response.data['delivery']['id']
        self.assertEqual(response.data['delivery']['status'], DeliveryStatus.ASSIGNED)
        self.assertEqual(self.assignment().status_code, 200)
        self.assertEqual(self.assignment(tracking_number='OTHER').status_code, 409)
        self.assertEqual(Delivery.objects.count(), 1)

        detail_page = reverse('sale_pages:delivery-detail', args=[delivery_id])
        detail_api = reverse('sale:delivery-detail', args=[delivery_id])
        self.assertRedirects(self.client.get(assignment_page), detail_page)
        self.assertContains(self.client.get(chalan_page), 'Track delivery')
        self.assertContains(self.client.get(detail_page), 'Generate a packing slip')
        self.assertEqual(self.transition(delivery_id, DeliveryStatus.DELIVERED).status_code, 409)
        response = self.transition(delivery_id, DeliveryStatus.HANDED_OVER)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('packing slip', str(response.data['status']))

        slip_response = self.client.post(reverse('sale:packing-slip-generate', args=[self.chalan.pk]), {}, format='json')
        self.assertEqual(slip_response.status_code, 201, slip_response.data)
        slip = slip_response.data['packing_slip']
        self.assertContains(self.client.get(detail_page), slip['slip_number'])
        self.assertEqual(self.client.get(detail_api).data['packing_slip'], slip['id'])

        response = self.transition(delivery_id, DeliveryStatus.HANDED_OVER, expected_status='assigned', note='Collected at the warehouse')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['changed'])
        self.assertIsNotNone(response.data['delivery']['stock_deducted_at'])
        self.assertFalse(self.transition(delivery_id, DeliveryStatus.HANDED_OVER, expected_status='assigned').data['changed'])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('8.499999'))
        self.assertEqual(self.transition(delivery_id, DeliveryStatus.IN_TRANSIT, expected_status='assigned').status_code, 409)
        response = self.client.patch(detail_api, {'courier': self.other_courier.pk}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.client.patch(detail_api, {'tracking_number': 'TRACK-106'}, format='json').status_code, 200)

        for state in (DeliveryStatus.IN_TRANSIT, DeliveryStatus.FAILED, DeliveryStatus.IN_TRANSIT, DeliveryStatus.DELIVERED):
            response = self.transition(delivery_id, state)
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.transition(delivery_id, DeliveryStatus.RETURNED).status_code, 409)
        record = self.client.get(detail_api).data
        self.assertEqual(record['status'], DeliveryStatus.DELIVERED)
        self.assertFalse(AccountTransaction.objects.exists())
        self.assertEqual(record['tracking_number'], 'TRACK-106')
        self.assertEqual(len(record['status_updates']), 6)
        self.assertEqual(record['status_updates'][-2]['note'], 'Collected at the warehouse')
        self.assertEqual(record['status_updates'][0]['create_by'], self.user.username)
        self.assertContains(self.client.get(detail_page), 'Status history')
        self.assertNotContains(self.client.get(detail_page), 'Record status')

        list_api = reverse('sale:delivery-list')
        self.assertEqual(self.client.get(list_api, {'q': 'TRACK-106', 'status': 'delivered'}).data['count'], 1)
        self.assertEqual(self.client.get(list_api, {'status': 'assigned'}).data['count'], 0)
        self.assertContains(self.client.get(reverse('sale_pages:delivery-list')), 'All statuses')
        self.assertTrue(AuditLog.objects.filter(resource='sale.delivery', action='delivered',
            reference=self.chalan.chalan_number, warehouse_id=self.warehouse.pk).exists())

    def test_assignment_and_updates_enforce_warehouse_scope_and_courier_eligibility(self):
        outside = Chalan.objects.create(warehouse=self.other_warehouse, customer=self.customer, shipping_address='Other')
        outside_delivery = Delivery.objects.create(chalan=outside, courier=self.courier)
        Courier.objects.filter(pk=self.courier.pk).update(is_active=False)
        self.assertEqual(self.assignment().status_code, 403)
        grant(self.role, 'view_chalan', 'view_delivery', 'add_delivery')
        self.assertEqual(self.assignment().status_code, 400)
        self.assertEqual(self.assignment(chalan=outside, courier=self.other_courier.pk).status_code, 404)
        self.assertEqual(self.client.get(reverse('sale:delivery-list')).data['count'], 0)
        self.assertEqual(self.client.get(reverse('sale:delivery-detail', args=[outside_delivery.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('sale_pages:delivery-detail', args=[outside_delivery.pk])).status_code, 404)
        response = self.assignment(courier=self.other_courier.pk)
        self.assertEqual(response.status_code, 201, response.data)
        delivery = response.data['delivery']
        self.assertEqual(self.transition(delivery['id'], DeliveryStatus.CANCELLED).status_code, 403)

        grant(self.role, 'change_delivery')
        self.assertEqual(self.transition(outside_delivery.pk, DeliveryStatus.HANDED_OVER).status_code, 404)
        self.assertEqual(self.transition(delivery['id'], DeliveryStatus.CANCELLED).status_code, 200)
        Courier.objects.filter(pk=self.courier.pk).update(is_active=True)
        detail_api = reverse('sale:delivery-detail', args=[delivery['id']])
        response = self.client.patch(detail_api, {'courier': self.courier.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['tracking_number'], '')
        self.assertEqual(self.transition(delivery['id'], DeliveryStatus.ASSIGNED).status_code, 200)
        PackingSlip.objects.create(chalan=self.chalan)
        self.assertNotIn('packing_slip', self.client.get(detail_api).data)
        self.assertEqual(self.transition(delivery['id'], DeliveryStatus.HANDED_OVER).status_code, 200)
        self.assertEqual(self.transition(delivery['id'], DeliveryStatus.RETURNED).status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))

    def test_handover_rolls_back_if_a_later_product_has_insufficient_stock(self):
        grant(self.role, 'view_chalan', 'view_delivery', 'add_delivery', 'change_delivery')
        other = Product.objects.create(sku='DEL-SHORT', name='Out of stock', currency_id='BDT', unit=self.unit)
        ChalanItem.objects.create(chalan=self.chalan, line_number=2, product=other, unit=self.unit, quantity=1)
        PackingSlip.objects.create(chalan=self.chalan)
        delivery_id = self.assignment().data['delivery']['id']

        response = self.transition(delivery_id, DeliveryStatus.HANDED_OVER)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(other.sku, str(response.data['quantity']))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))
        delivery = Delivery.objects.get(pk=delivery_id)
        self.assertEqual(delivery.status, DeliveryStatus.ASSIGNED)
        self.assertIsNone(delivery.stock_deducted_at)
        self.assertEqual(delivery.status_updates.count(), 1)

    def test_delivery_credits_the_selected_account_once_in_base_currency(self):
        grant(self.role, 'view_chalan', 'view_delivery', 'add_delivery', 'change_delivery')
        Currency.objects.create(code='USD', name='USD', exchange_rate=100)
        Chalan.objects.filter(pk=self.chalan.pk).update(currency_id='USD', exchange_rate=100, shipping_cost=2)
        ChalanItem.objects.filter(pk=self.item.pk).update(unit_price=10, discount_amount=1, tax_rate=10)
        account = FinancialAccount.objects.create(name='Warehouse collections', type='bank', warehouse=self.warehouse,
                                                   opening_balance=50)
        wrong_account = FinancialAccount.objects.create(name='Another warehouse account', type='bank', warehouse=self.other_warehouse)
        full_account = FinancialAccount.objects.create(name='Account at ledger limit', type='bank', warehouse=self.warehouse,
                                                        opening_balance=MAX_AMOUNT)
        PackingSlip.objects.create(chalan=self.chalan)
        delivery_id = self.assignment().data['delivery']['id']
        response = self.transition(delivery_id, DeliveryStatus.HANDED_OVER)
        self.assertEqual(response.status_code, 200, response.data)
        detail_page = reverse('sale_pages:delivery-detail', args=[delivery_id])
        self.assertContains(self.client.get(detail_page), account.name)
        self.assertNotContains(self.client.get(detail_page), wrong_account.name)
        self.assertFalse(AccountTransaction.objects.exists())

        for account_data in ({}, {'payment_account': wrong_account.pk}, {'payment_account': full_account.pk}):
            response = self.transition(delivery_id, DeliveryStatus.DELIVERED, **account_data)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(Delivery.objects.get(pk=delivery_id).status, DeliveryStatus.HANDED_OVER)
            self.assertFalse(ChalanPayment.objects.exists())
            self.assertFalse(AccountTransaction.objects.exists())

        response = self.transition(delivery_id, DeliveryStatus.DELIVERED, payment_account=account.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(self.transition(delivery_id, DeliveryStatus.DELIVERED, payment_account=account.pk).data['changed'])
        payment = ChalanPayment.objects.get(chalan=self.chalan)
        entry = AccountTransaction.objects.get(chalan_payment=payment)
        self.assertEqual((entry.account, entry.direction, entry.amount, entry.balance_after),
                         (account, 'inflow', Decimal('1740.0000'), Decimal('1790.0000')))
        self.assertEqual(payment.reference, self.chalan.chalan_number)
        self.assertContains(self.client.get(detail_page), 'BDT 1740.0000')
        self.assertEqual(self.transition(delivery_id, DeliveryStatus.RETURNED).status_code, 409)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('8.499999'))

        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='accounting',
            codename__in=['view_financialaccount', 'view_accounttransaction']))
        statement = self.client.get(reverse('accounting:account-statement', args=[account.pk]), {'q': self.chalan.chalan_number})
        self.assertEqual(statement.status_code, 200, statement.data)
        self.assertEqual(statement.data['results'][0]['source'], f'Chalan payment {payment.number}')
        report = self.client.get(reverse('accounting:daybook'), {'source': 'chalan'})
        self.assertEqual(report.json()['entries'][0]['voucher'], payment.number)

    def test_return_restores_dispatched_quantities_and_legacy_return_leaves_stock_alone(self):
        grant(self.role, 'view_chalan', 'view_delivery', 'add_delivery', 'change_delivery', 'change_chalan',
              'change_chalanitem', 'delete_chalan', 'delete_chalanitem')
        ChalanItem.objects.create(chalan=self.chalan, line_number=2, product=self.product, unit=self.unit, quantity='0.499999')
        PackingSlip.objects.create(chalan=self.chalan)
        delivery_id = self.assignment().data['delivery']['id']
        self.assertEqual(self.transition(delivery_id, DeliveryStatus.HANDED_OVER).status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('8'))
        chalan_api = reverse('sale:chalan-detail', args=[self.chalan.pk])
        self.assertTrue(self.client.get(chalan_api).data['stock_in_transit'])
        self.assertEqual(self.client.delete(chalan_api).status_code, 409)
        line = {'id': self.item.pk, 'line_number': 1, 'product': self.product.pk, 'unit': self.unit.pk, 'quantity': 2}
        response = self.client.patch(chalan_api, {'items': [line]}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('locked', str(response.data['items']))
        self.assertEqual(self.client.patch(chalan_api, {'notes': 'Awaiting return'}, format='json').status_code, 200)

        self.assertEqual(self.transition(delivery_id, DeliveryStatus.FAILED).status_code, 200)
        self.assertEqual(self.transition(delivery_id, DeliveryStatus.RETURNED).status_code, 200)
        self.assertFalse(self.transition(delivery_id, DeliveryStatus.RETURNED).data['changed'])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))
        self.assertFalse(self.client.get(chalan_api).data['is_delete_locked'])

        legacy = Chalan.objects.create(customer=self.customer, warehouse=self.warehouse, shipping_address='Legacy')
        ChalanItem.objects.create(chalan=legacy, line_number=1, product=self.product, unit=self.unit, quantity=2)
        old_delivery = Delivery.objects.create(chalan=legacy, courier=self.courier, status=DeliveryStatus.HANDED_OVER)
        self.assertEqual(self.transition(old_delivery.pk, DeliveryStatus.RETURNED).status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))
        self.assertFalse(ChalanPayment.objects.exists())
