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

from accounting.models import AccountTransaction, FinancialAccount, SalesPayment
from accounting.services import post_transaction, post_transfer
from common.models import ProductStatus, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from sale.models import SalesOrder, SalesOrderItem, SalesReturn, SalesReturnItem
from sale.returns import RETURN_CREATE, RETURN_EDIT, ReturnBusy, complete_return
from sale.services import complete_sale
from warehouse.models import Stock, WareHouse
from warehouse.tests.helpers import assign_warehouse_role


class SalesReturnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create_user(username='returns-operator')
        cls.admin = get_user_model().objects.create_superuser(username='returns-admin')
        cls.warehouse = WareHouse.objects.create(name='Main', address='Main address', phone='1')
        cls.branch = WareHouse.objects.create(name='Branch', address='Branch address', phone='2')
        cls.role = assign_warehouse_role(cls.actor, warehouse=cls.warehouse).role
        cls.unit = UnitOfMeasure.objects.create(code='RETURN-PCS', name='Pieces')
        cls.product = Product.objects.create(sku='RETURN-PRODUCT', name='Product', currency_id='BDT', unit=cls.unit)
        cls.stock = Stock.objects.create(warehouse=cls.warehouse, product=cls.product, quantity=10, create_by='initial')
        cls.account = FinancialAccount.objects.create(name='Sales bank', type='bank', warehouse=cls.warehouse)
        cls.order = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=cls.warehouse,
                                             payment_account=cls.account)
        cls.sold = SalesOrderItem.objects.create(sales_order=cls.order, product=cls.product, unit=cls.unit, line_number=1,
                                                quantity=Decimal('5.123456'), unit_price=10)
        complete_sale(cls.admin, cls.order.pk)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.actor)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.grant(*RETURN_CREATE, *RETURN_EDIT, 'sale.delete_salesreturn')

    def grant(self, *permissions):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='sale',
            codename__in=[permission.split('.')[1] for permission in permissions]))

    def create_return(self, quantity='1', expected=201, **changes):
        payload = {'sales_order': self.order.pk, 'items': [{'sales_order_item': self.sold.pk, 'quantity': quantity}], **changes}
        response = self.client.post(reverse('sale:sales-return-list'), payload, format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, expected, response.data)
        return response.data.get('sales_return', response.data)

    def complete(self, pk, expected=200):
        response = self.client.post(reverse('sale:sales-return-complete'), {'return_id': pk, 'is_completed': True},
                                    format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    def test_partial_returns_restock_only_once_and_keep_the_original_sale(self):
        branch_stock = Stock.objects.create(warehouse=self.branch, product=self.product, quantity=100)
        self.order.refresh_from_db()
        fulfilled_at = self.order.fulfilled_at
        record = self.create_return('1.123456', is_completed=True)
        self.assertFalse(record['is_completed'])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('4.876544'))
        first = self.complete(record['id'])
        self.assertTrue(first['completed_now'])
        self.assertFalse(self.complete(record['id'])['completed_now'])
        refund = SalesPayment.objects.get(sales_return_id=record['id'])
        self.assertEqual(refund.amount, Decimal('11.2346'))
        self.assertEqual(refund.account_id, self.account.pk)
        self.assertEqual(refund.transaction.direction, AccountTransaction.Direction.OUTFLOW)
        self.assertEqual(refund.transaction.balance_after, Decimal('40.0000'))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('6'))
        self.assertEqual(self.stock.create_by, 'initial')
        self.assertEqual(self.stock.modified_by, self.actor.username)

        self.create_return('4.000001', expected=400)
        self.complete(self.create_return('4')['id'])
        self.create_return('0.000001', expected=400)
        self.stock.refresh_from_db()
        branch_stock.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.stock.quantity, branch_stock.quantity), (Decimal('10'), Decimal('100')))
        self.assertEqual(self.order.fulfilled_at, fulfilled_at)
        self.assertEqual(self.account.transactions.first().balance_after, Decimal('0.0000'))
        detail = reverse('sale:sales-return-detail', args=[record['id']])
        self.assertEqual(self.client.patch(detail, {'notes': 'Change'}, format='json', HTTP_X_CSRFTOKEN=self.token).status_code, 409)
        self.assertEqual(self.client.delete(detail, HTTP_X_CSRFTOKEN=self.token).status_code, 409)
        self.assertEqual(self.client.get(detail).data['items'][0]['quantity'], '1.123456')
        self.assertEqual(self.client.get(detail).data['refund_amount'], '11.2346')
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='accounting', codename__in=[
            'view_financialaccount', 'view_accounttransaction', 'view_salespayment',
        ]))
        statement_url = reverse('accounting:account-statement', args=[self.account.pk])
        row, = self.client.get(statement_url, {'q': record['return_number']}).data['results']
        self.assertEqual((row['source'], row['sales_payment'], row['direction']),
                         (f'Sales refund {refund.number}', refund.pk, 'outflow'))
        cash_flow = self.client.get(reverse('accounting:cash-flow-list'), {'account': self.account.pk})
        self.assertEqual(cash_flow.status_code, 200, cash_flow.data)
        self.assertEqual([(entry['direction'], entry['amount']) for entry in cash_flow.data['results']], [
            ('outflow', '40.0000'), ('outflow', '11.2346'), ('inflow', '51.2346'),
        ])
        payments_page = self.client.get(reverse('sale_pages:sales-payment-list', args=[self.order.pk]))
        self.assertEqual(payments_page.context['refunded'], Decimal('51.2346'))
        self.assertEqual(payments_page.context['due'], Decimal('0.0000'))

    def test_partial_refunds_reconcile_original_discount_tax_and_exchange_rate(self):
        currency = Currency.objects.create(code='EUR', name='Euro', exchange_rate=10)
        order = SalesOrder.objects.create(warehouse=self.warehouse, payment_account=self.account,
                                          currency=currency, exchange_rate='1.1', shipping_cost='0.5')
        item = order.items.create(product=self.product, unit=self.unit, line_number=1, quantity=3, unit_price=1,
                                  discount_amount='0.2', tax_rate=25)
        complete_sale(self.admin, order.pk)

        amounts = []
        for _ in range(3):
            record = self.create_return(sales_order=order.pk, items=[{'sales_order_item': item.pk, 'quantity': '1'}])
            self.complete(record['id'])
            amounts.append(SalesPayment.objects.get(sales_return_id=record['id']).amount)

        self.assertEqual(amounts, [Decimal('1.2833'), Decimal('1.2834'), Decimal('1.2833')])
        receipt = order.payments.get(sales_return__isnull=True)
        self.assertEqual(receipt.amount - sum(amounts), Decimal('0.5500'))

    def test_refund_is_limited_to_legacy_payments_and_uses_their_original_accounts(self):
        order = SalesOrder.objects.create(warehouse=self.warehouse, currency_id='BDT', exchange_rate=1,
                                          is_completed=True)
        item = order.items.create(product=self.product, unit=self.unit, line_number=1, quantity=2, unit_price=10)
        other = FinancialAccount.objects.create(name='Other sales bank', type='bank', warehouse=self.warehouse)
        date = timezone.localdate()
        for account, amount in [(self.account, Decimal('3')), (other, Decimal('4'))]:
            payment = order.payments.create(account=account, amount=amount, method=account.type)
            post_transaction(account, AccountTransaction.Direction.INFLOW, self.admin, date=date, amount=amount,
                             sales_payment=payment)

        record = self.create_return(sales_order=order.pk, items=[{'sales_order_item': item.pk, 'quantity': '1'}])
        self.complete(record['id'])
        refunds = order.payments.filter(sales_return_id=record['id']).values_list('account_id', 'amount')
        self.assertCountEqual(refunds, [(self.account.pk, Decimal('3')), (other.pk, Decimal('4'))])
        record = self.create_return(sales_order=order.pk, items=[{'sales_order_item': item.pk, 'quantity': '1'}])
        self.complete(record['id'])
        self.assertFalse(order.payments.filter(sales_return_id=record['id']).exists())

    def test_insufficient_refund_funds_roll_back_completion_and_restock(self):
        other = FinancialAccount.objects.create(name='Other bank', type='bank', warehouse=self.warehouse)
        post_transfer(
            self.admin, source_account=self.account.pk, destination_account=other.pk, amount=Decimal('50'),
            date=timezone.localdate(), reference='', request_key=uuid4(),
        )
        record = self.create_return()
        response = self.complete(record['id'], expected=400)
        self.assertIn('amount', response)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('4.876544'))
        self.assertFalse(SalesReturn.objects.get(pk=record['id']).is_completed)
        self.assertFalse(SalesPayment.objects.filter(sales_return_id=record['id']).exists())

    def test_competing_draft_rechecks_remaining_and_can_be_edited(self):
        first, second = self.create_return('3'), self.create_return('3')
        self.complete(first['id'])
        self.complete(second['id'], expected=400)
        second_row = SalesReturn.objects.get(pk=second['id'])
        self.assertFalse(second_row.is_completed)
        self.assertIsNone(second_row.completed_at)
        line_id = second_row.items.get().pk
        response = self.client.patch(reverse('sale:sales-return-update', args=[second['id']]),
            {'items': [{'sales_order_item': self.sold.pk, 'quantity': '2.123456'}]}, format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(second_row.items.get().pk, line_id)
        self.complete(second['id'])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))

    def test_invalid_source_lines_non_returnable_products_and_warehouse_scope(self):
        unfinished = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=self.warehouse)
        branch_sale = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1, warehouse=self.branch, is_completed=True)
        foreign_item = SalesOrderItem.objects.create(sales_order=branch_sale, product=self.product, unit=self.unit,
                                                     line_number=1, quantity=1, unit_price=1)
        self.create_return(expected=400, sales_order=unfinished.pk)
        self.create_return(expected=400, sales_order=branch_sale.pk)
        self.create_return(expected=400, items=[{'sales_order_item': foreign_item.pk, 'quantity': '1'}])
        self.create_return(expected=400, items=[{'sales_order_item': self.sold.pk, 'quantity': '1'}] * 2)
        hidden_record = SalesReturn.objects.create(sales_order=branch_sale)
        self.complete(hidden_record.pk, expected=404)
        self.assertEqual(self.client.get(reverse('sale_pages:return-detail', args=[hidden_record.pk])).status_code, 404)

        record = self.create_return()
        Product.objects.filter(pk=self.product.pk).update(is_returnable=False)
        self.create_return(expected=400)
        self.complete(record['id'], expected=400)
        self.assertFalse(SalesReturn.objects.get(pk=record['id']).is_completed)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('4.876544'))

    def test_stock_limit_on_later_product_rolls_back_the_entire_return(self):
        product = Product.objects.create(sku='RETURN-OTHER', name='Other product', currency_id='BDT', unit=self.unit)
        line = SalesOrderItem.objects.create(sales_order=self.order, line_number=2, product=product, unit=self.unit,
                                             quantity=2, unit_price=1)
        full_stock = Stock.objects.create(warehouse=self.warehouse, product=product, quantity=999999999999)
        record = self.create_return(items=[{'sales_order_item': self.sold.pk, 'quantity': '1'},
                                           {'sales_order_item': line.pk, 'quantity': '2'}])
        self.complete(record['id'], expected=400)
        self.stock.refresh_from_db()
        full_stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('4.876544'))
        self.assertEqual(full_stock.quantity, Decimal('999999999999'))
        self.assertFalse(SalesReturn.objects.get(pk=record['id']).is_completed)

    def test_completion_rechecks_warehouse_and_product_unit(self):
        record = self.create_return()
        WareHouse.objects.filter(pk=self.warehouse.pk).update(is_active=False)
        self.assertIn('warehouse', self.complete(record['id'], expected=400))
        WareHouse.objects.filter(pk=self.warehouse.pk).update(is_active=True)
        other_unit = UnitOfMeasure.objects.create(code='RETURN-BOX', name='Box')
        Product.objects.filter(pk=self.product.pk).update(unit=other_unit)
        self.assertIn('items', self.complete(record['id'], expected=400))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('4.876544'))

    def test_pages_and_options_follow_role_permissions_and_use_existing_api_patterns(self):
        listing = self.client.get(reverse('sale_pages:return-list'))
        self.assertContains(listing, 'Sales returns')
        self.assertContains(listing, 'data-complete-id-field="return_id"')
        sales_page = self.client.get(reverse('sale_pages:sale-detail', args=[self.order.pk]))
        self.assertContains(sales_page, f'?sales_order={self.order.pk}')
        form = self.client.get(reverse('sale_pages:create-return'))
        self.assertContains(form, f'action="{reverse("sale:sales-return-list")}"')
        self.assertContains(form, f'data-success-url="{reverse("sale_pages:return-list")}"')
        options = reverse('sale:sales-return-options')
        self.assertEqual([item['value'] for item in self.client.get(options).data['orders']], [self.order.pk])
        self.assertEqual(self.client.get(options, {'sales_order': self.order.pk}).data['lines'][0]['remaining_quantity'], '5.123456')
        record = self.create_return()
        detail_url = reverse('sale:sales-return-detail', args=[record['id']])
        self.assertContains(self.client.get(reverse('sale_pages:return-detail', args=[record['id']])),
                            f'data-api-url="{detail_url}"')
        self.assertContains(self.client.get(reverse('sale_pages:return-detail', args=[record['id']])), 'Refunded amount')
        self.assertEqual(self.client.post(reverse('sale:sales-return-complete'), {
            'return_id': record['id'], 'is_completed': True,
        }, format='json').status_code, 403)
        self.role.permissions.clear()
        self.grant('sale.view_salesreturn')
        self.assertNotIn('items', self.client.get(detail_url).data)
        self.assertEqual(self.client.get(options).status_code, 403)
        self.complete(record['id'], expected=403)
        self.grant('sale.add_salesreturn', 'sale.add_salesreturnitem')
        self.create_return(expected=403)


class SalesReturnConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='returns-race-admin')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        warehouse = WareHouse.objects.create(name='Race warehouse', address='Address', phone='1')
        unit = UnitOfMeasure.objects.create(code='RETURN-RACE-PCS', name='Pieces')
        self.product = Product.objects.create(sku='RETURN-RACE', name='Product', currency_id='BDT', unit=unit)
        self.order = SalesOrder.objects.create(warehouse=warehouse, currency_id='BDT', exchange_rate=1, is_completed=True)
        self.sold = self.order.items.create(product=self.product, unit=unit, line_number=1, quantity=4, unit_price=1)

    def draft(self):
        record = SalesReturn.objects.create(sales_order=self.order)
        SalesReturnItem.objects.create(sales_return=record, sales_order_item=self.sold, quantity=3)
        return record.pk

    def race(self, pks):
        barrier = Barrier(len(pks))

        def complete(pk):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.admin.pk)
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

        with ThreadPoolExecutor(max_workers=len(pks)) as pool:
            return sorted(pool.map(complete, pks))

    def test_retries_and_distinct_drafts_cannot_double_restock(self):
        first, second = self.draft(), self.draft()
        self.assertEqual(self.race([first, first]), ['completed', 'repeated'])
        self.assertEqual(self.race([second, second]), ['blocked', 'blocked'])
        self.assertEqual(Stock.objects.get(product=self.product).quantity, Decimal('3'))
        self.assertEqual(SalesReturn.objects.filter(is_completed=True).count(), 1)

    def test_simultaneous_distinct_returns_share_one_quantity_limit(self):
        self.assertEqual(self.race([self.draft(), self.draft()]), ['blocked', 'completed'])
        self.assertEqual(Stock.objects.get(product=self.product).quantity, Decimal('3'))
        self.assertEqual(SalesReturn.objects.filter(is_completed=True).count(), 1)
