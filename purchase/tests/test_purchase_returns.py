from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import sleep
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounting.models import MAX_AMOUNT, AccountTransaction, FinancialAccount, PurchasePayment
from accounting.services import post_transfer
from common.models import ProductStatus, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from purchase.models import PurchaseOrder, PurchaseReturn
from purchase.returns import RETURN_CREATE, RETURN_EDIT, ReturnBusy, complete_return
from purchase.services import receive_purchase
from warehouse.models import Stock, WareHouse
from warehouse.tests.helpers import assign_warehouse_role


class PurchaseReturnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create_user(username='purchase-return-operator')
        cls.admin = get_user_model().objects.create_superuser(username='purchase-return-admin')
        cls.warehouse = WareHouse.objects.create(name='Main', address='Main address', phone='1')
        cls.branch = WareHouse.objects.create(name='Branch', address='Branch address', phone='2')
        cls.role = assign_warehouse_role(cls.actor, warehouse=cls.warehouse).role
        cls.unit = UnitOfMeasure.objects.create(code='PR-PCS', name='Pieces')
        cls.product = Product.objects.create(sku='PR-PRODUCT', name='Product', currency_id='BDT', unit=cls.unit)
        cls.stock = Stock.objects.create(warehouse=cls.warehouse, product=cls.product, quantity=10, create_by='initial')
        cls.account = FinancialAccount.objects.create(name='Purchase bank', type='bank', warehouse=cls.warehouse,
                                                      opening_balance=100)
        cls.order = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=cls.warehouse,
                                                  payment_account=cls.account)
        cls.received = cls.order.items.create(product=cls.product, unit=cls.unit, line_number=1,
                                              quantity=Decimal('5.123456'), unit_price=10)
        receive_purchase(cls.admin, cls.order.pk)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.actor)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.grant(*RETURN_CREATE, *RETURN_EDIT, 'purchase.delete_purchasereturn')

    def grant(self, *permissions):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='purchase',
            codename__in=[permission.split('.')[1] for permission in permissions]))

    def create_return(self, quantity='1', expected=201, **changes):
        payload = {'purchase_order': self.order.pk,
                   'items': [{'purchase_order_item': self.received.pk, 'quantity': quantity}], **changes}
        response = self.client.post(reverse('purchase:purchase-return-list'), payload, format='json',
                                    HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, expected, response.data)
        return response.data.get('purchase_return', response.data)

    def complete(self, pk, expected=200):
        response = self.client.post(reverse('purchase:purchase-return-complete'), {'return_id': pk, 'is_completed': True},
                                    format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    def test_partial_and_full_returns_deduct_once_from_original_warehouse(self):
        branch_stock = Stock.objects.create(warehouse=self.branch, product=self.product, quantity=100)
        self.order.refresh_from_db()
        received_at = self.order.updated_at
        record = self.create_return('1.123456', is_completed=True)
        self.assertFalse(record['is_completed'])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('15.123456'))
        self.assertTrue(self.complete(record['id'])['completed_now'])
        self.assertFalse(self.complete(record['id'])['completed_now'])
        refund = PurchasePayment.objects.get(purchase_return_id=record['id'])
        self.assertEqual((refund.amount, refund.account_id), (Decimal('11.2346'), self.account.pk))
        self.assertEqual(refund.transaction.direction, AccountTransaction.Direction.INFLOW)
        self.assertEqual(refund.transaction.balance_after, Decimal('60.0000'))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('14'))
        self.assertEqual(self.stock.create_by, 'initial')
        self.assertEqual(self.stock.modified_by, self.actor.username)

        self.create_return('4.000001', expected=400)
        self.complete(self.create_return('4')['id'])
        self.create_return('0.000001', expected=400)
        self.stock.refresh_from_db()
        branch_stock.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.stock.quantity, branch_stock.quantity), (Decimal('10'), Decimal('100')))
        self.assertTrue(self.order.is_received)
        self.assertEqual(self.order.updated_at, received_at)
        self.assertEqual(self.account.transactions.first().balance_after, Decimal('100.0000'))
        detail = reverse('purchase:purchase-return-detail', args=[record['id']])
        self.assertEqual(self.client.patch(detail, {'notes': 'Change'}, format='json',
                                           HTTP_X_CSRFTOKEN=self.token).status_code, 409)
        self.assertEqual(self.client.delete(detail, HTTP_X_CSRFTOKEN=self.token).status_code, 409)
        self.assertEqual(self.client.get(detail).data['items'][0]['quantity'], '1.123456')
        self.assertEqual(self.client.get(detail).data['refund_amount'], '11.2346')
        original = reverse('purchase:purchase-order-detail', args=[self.order.pk])
        self.assertEqual(self.client.get(original).data['payment_amount'], '51.2346')
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='accounting', codename__in=[
            'view_financialaccount', 'view_accounttransaction',
        ]))
        statement = reverse('accounting:account-statement', args=[self.account.pk])
        row, = self.client.get(statement, {'q': record['return_number']}).data['results']
        self.assertEqual((row['source'], row['purchase_payment'], row['direction']),
                         (f'Purchase refund {refund.number}', refund.pk, 'inflow'))
        cash_flow = self.client.get(reverse('accounting:cash-flow-list'), {'account': self.account.pk})
        self.assertEqual(cash_flow.status_code, 200, cash_flow.data)
        self.assertEqual([(entry['direction'], entry['amount']) for entry in cash_flow.data['results']], [
            ('inflow', '40.0000'), ('inflow', '11.2346'), ('outflow', '51.2346'),
        ])

    def test_fractional_refunds_use_the_original_discount_tax_and_exchange_rate(self):
        currency = Currency.objects.create(code='EUR', name='Euro', exchange_rate=10)
        order = PurchaseOrder.objects.create(warehouse=self.warehouse, payment_account=self.account,
                                             currency=currency, exchange_rate='1.1', shipping_cost='0.5')
        item = order.items.create(product=self.product, unit=self.unit, line_number=1, quantity=3, unit_price=1,
                                  discount_amount='0.2', tax_rate=25)
        receive_purchase(self.admin, order.pk)

        amounts = []
        for _ in range(3):
            record = self.create_return(purchase_order=order.pk,
                                        items=[{'purchase_order_item': item.pk, 'quantity': '1'}])
            self.complete(record['id'])
            amounts.append(PurchasePayment.objects.get(purchase_return_id=record['id']).amount)

        self.assertEqual(amounts, [Decimal('1.2833'), Decimal('1.2834'), Decimal('1.2833')])
        payment = order.payments.get(purchase_return__isnull=True)
        self.assertEqual(payment.amount - sum(amounts), Decimal('0.5500'))

    def test_legacy_purchase_without_a_recorded_payment_returns_stock_without_a_refund(self):
        order = PurchaseOrder.objects.create(warehouse=self.warehouse, currency_id='BDT', exchange_rate=1,
                                             is_received=True)
        item = order.items.create(product=self.product, unit=self.unit, line_number=1, quantity=1, unit_price=10)
        record = self.create_return(purchase_order=order.pk, items=[{'purchase_order_item': item.pk, 'quantity': '1'}])
        self.complete(record['id'])
        self.assertFalse(order.payments.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('14.123456'))

    def test_refund_exceeding_account_balance_limit_rolls_back_the_return(self):
        other = FinancialAccount.objects.create(name='Other bank', type='bank', warehouse=self.warehouse,
                                                opening_balance=MAX_AMOUNT)
        balance = self.account.transactions.first().balance_after
        post_transfer(
            self.admin, source_account=other.pk, destination_account=self.account.pk,
            amount=MAX_AMOUNT - balance, date=timezone.localdate(), reference='', request_key=uuid4(),
        )
        record = self.create_return()
        self.assertIn('amount', self.complete(record['id'], expected=400))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('15.123456'))
        self.assertFalse(PurchaseReturn.objects.get(pk=record['id']).is_completed)
        self.assertFalse(PurchasePayment.objects.filter(purchase_return_id=record['id']).exists())

    def test_second_draft_can_be_edited_after_first_return_completes(self):
        first, second = self.create_return('3'), self.create_return('3')
        self.complete(first['id'])
        self.complete(second['id'], expected=400)
        draft = PurchaseReturn.objects.get(pk=second['id'])
        self.assertFalse(draft.is_completed)
        self.assertIsNone(draft.completed_at)
        line_id = draft.items.get().pk
        response = self.client.patch(reverse('purchase:purchase-return-update', args=[draft.pk]),
            {'items': [{'purchase_order_item': self.received.pk, 'quantity': '2.123456'}]}, format='json',
            HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(draft.items.get().pk, line_id)
        self.complete(draft.pk)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))

    def test_insufficient_stock_rolls_back_all_lines_and_allows_retry(self):
        product = Product.objects.create(sku='PR-OTHER', name='Other product', currency_id='BDT', unit=self.unit)
        line = self.order.items.create(line_number=2, product=product, unit=self.unit, quantity=2, unit_price=1)
        Stock.objects.create(warehouse=self.branch, product=product, quantity=100)
        record = self.create_return(items=[{'purchase_order_item': self.received.pk, 'quantity': '1'},
                                           {'purchase_order_item': line.pk, 'quantity': '2'}])
        self.assertIn('quantity', self.complete(record['id'], expected=400))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('15.123456'))
        draft = PurchaseReturn.objects.get(pk=record['id'])
        self.assertFalse(draft.is_completed)
        self.assertIsNone(draft.completed_at)
        Stock.objects.create(warehouse=self.warehouse, product=product, quantity=2)
        self.complete(draft.pk)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('14.123456'))
        self.assertEqual(Stock.objects.get(warehouse=self.warehouse, product=product).quantity, Decimal('0'))

    def test_invalid_receipt_lines_and_warehouse_scope(self):
        unreceived = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=self.warehouse)
        outside = PurchaseOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=self.branch, is_received=True)
        outside_line = outside.items.create(line_number=1, product=self.product, unit=self.unit, quantity=1, unit_price=1)
        self.create_return(expected=400, purchase_order=unreceived.pk)
        self.create_return(expected=400, purchase_order=outside.pk)
        self.create_return(expected=400, items=[{'purchase_order_item': outside_line.pk, 'quantity': '1'}])
        self.create_return(expected=400, items=[{'purchase_order_item': self.received.pk, 'quantity': '1'}] * 2)
        self.create_return(expected=400, return_date='2000-01-01')
        outside_return = PurchaseReturn.objects.create(purchase_order=outside)
        self.complete(outside_return.pk, expected=404)
        self.assertEqual(self.client.get(reverse('purchase_pages:return-detail', args=[outside_return.pk])).status_code, 404)

        draft = self.create_return()
        WareHouse.objects.filter(pk=self.warehouse.pk).update(is_active=False)
        self.assertIn('warehouse', self.complete(draft['id'], expected=400))
        WareHouse.objects.filter(pk=self.warehouse.pk).update(is_active=True)
        Product.objects.filter(pk=self.product.pk).update(is_returnable=False)
        self.create_return(expected=400)
        self.assertIn('items', self.complete(draft['id'], expected=400))
        self.assertFalse(PurchaseReturn.objects.get(pk=draft['id']).is_completed)

    def test_pages_options_csrf_and_role_permissions(self):
        listing = self.client.get(reverse('purchase_pages:return-list'))
        self.assertContains(listing, 'Purchase returns')
        self.assertContains(listing, 'data-complete-result-key="purchase_return"')
        purchase_page = self.client.get(reverse('purchase_pages:purchase-detail', args=[self.order.pk]))
        self.assertContains(purchase_page, f'?purchase_order={self.order.pk}')
        form = self.client.get(reverse('purchase_pages:create-return'), {'purchase_order': self.order.pk})
        self.assertContains(form, 'data-return-type="purchase"')
        self.assertContains(form, 'name="purchase_order"')
        self.assertContains(form, f'action="{reverse("purchase:purchase-return-list")}"')
        options = reverse('purchase:purchase-return-options')
        self.assertEqual([item['value'] for item in self.client.get(options).data['orders']], [self.order.pk])
        line = self.client.get(options, {'purchase_order': self.order.pk}).data['lines'][0]
        self.assertEqual(line['received_quantity'], '5.123456')
        self.assertEqual(line['remaining_quantity'], '5.123456')
        returned = self.create_return()
        detail_url = reverse('purchase:purchase-return-detail', args=[returned['id']])
        detail_page = self.client.get(reverse('purchase_pages:return-detail', args=[returned['id']]))
        self.assertContains(detail_page, f'data-api-url="{detail_url}"')
        self.assertContains(detail_page, 'Complete return')
        self.assertContains(detail_page, 'Refunded amount')
        self.assertEqual(self.client.post(reverse('purchase:purchase-return-complete'), {
            'return_id': returned['id'], 'is_completed': True,
        }, format='json').status_code, 403)
        self.role.permissions.clear()
        self.grant('purchase.view_purchasereturn')
        self.assertContains(self.client.get(reverse('purchase_pages:return-list')), 'Purchase returns')
        self.assertNotIn('items', self.client.get(detail_url).data)
        self.assertEqual(self.client.get(options).status_code, 403)
        self.complete(returned['id'], expected=403)
        self.grant('purchase.add_purchasereturn', 'purchase.add_purchasereturnitem')
        self.create_return(expected=403)


class PurchaseReturnConcurrencyTests(TransactionTestCase):
    def test_simultaneous_completion_cannot_deduct_stock_twice(self):
        admin = get_user_model().objects.create_superuser(username='purchase-return-race-admin')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        warehouse = WareHouse.objects.create(name='Race warehouse', address='Address', phone='1')
        unit = UnitOfMeasure.objects.create(code='PR-RACE-PCS', name='Pieces')
        product = Product.objects.create(sku='PR-RACE', name='Product', currency_id='BDT', unit=unit)
        account = FinancialAccount.objects.create(name='Race bank', type='bank', warehouse=warehouse, opening_balance=10)
        order = PurchaseOrder.objects.create(warehouse=warehouse, currency_id='BDT', exchange_rate=1,
                                             payment_account=account)
        item = order.items.create(product=product, unit=unit, line_number=1, quantity=4, unit_price=1)
        receive_purchase(admin, order.pk)
        drafts = [PurchaseReturn.objects.create(purchase_order=order) for _ in range(2)]
        for draft in drafts:
            draft.items.create(purchase_order_item=item, quantity=3)
        barrier = Barrier(2)

        def complete(pk):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=admin.pk)
                barrier.wait(timeout=10)
                for attempt in range(20):
                    try:
                        return 'completed' if complete_return(actor, pk)[1] else 'repeated'
                    except ReturnBusy:
                        if attempt == 19:
                            raise
                        sleep(0.02)
                    except ValidationError:
                        return 'blocked'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(complete, [draft.pk for draft in drafts])), ['blocked', 'completed'])
        self.assertEqual(PurchaseReturn.objects.filter(is_completed=True).count(), 1)
        self.assertEqual(Stock.objects.get(warehouse=warehouse, product=product).quantity, Decimal('1'))
        self.assertEqual(order.payments.filter(purchase_return__isnull=False).count(), 1)
