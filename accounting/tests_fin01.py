from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import AccountTransaction, FinancialAccount, PurchasePayment, SalesPayment
from accounting.services import accounts_with_balances, compute_order_balance
from common.models import PaymentStatus, SettlementMode, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from common.models import ProductStatus
from people.models import Party
from purchase.models import PurchaseOrder, PurchaseOrderItem, PurchaseReturn, PurchaseReturnItem
from purchase.returns import complete_return as complete_purchase_return
from purchase.services import receive_purchase
from sale.models import SalesOrder, SalesOrderItem, SalesReturn, SalesReturnItem
from sale.returns import complete_return as complete_sale_return
from sale.services import complete_sale
from warehouse.models import Stock, WareHouse
from warehouse.tests.helpers import assign_warehouse_role


class Fin01SettlementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='fin01-admin')
        cls.branch_user = get_user_model().objects.create_user(username='fin01-branch')
        cls.wh = WareHouse.objects.create(name='FIN Main', address='Dhaka', phone='017')
        cls.other_wh = WareHouse.objects.create(name='FIN Branch', address='Chattogram', phone='018')
        cls.role = assign_warehouse_role(cls.branch_user, warehouse=cls.wh).role
        cls.role.permissions.add(*Permission.objects.filter(
            codename__in=[
                'view_salesorder', 'change_salesorder', 'view_financialaccount',
                'view_salespayment', 'add_sales_payment', 'view_party',
            ]
        ))
        cls.cash = FinancialAccount.objects.create(
            name='FIN Cash', type=FinancialAccount.Type.CASH, warehouse=cls.wh,
            opening_balance=Decimal('2000.0000'), is_default=True,
        )
        cls.card_bank = FinancialAccount.objects.create(
            name='FIN Card Bank', type=FinancialAccount.Type.BANK, warehouse=cls.wh,
            opening_balance=Decimal('1000.0000'), account_number='ACC-1001',
        )
        cls.remote_cash = FinancialAccount.objects.create(
            name='FIN Remote Cash', type=FinancialAccount.Type.CASH, warehouse=cls.other_wh,
            opening_balance=Decimal('500.0000'),
        )
        cls.unit = UnitOfMeasure.objects.create(name='Pieces', code='FIN-PCS')
        cls.product = Product.objects.create(
            name='FIN Product', sku='FIN-SKU', unit=cls.unit, currency_id='BDT',
            cost_price=Decimal('60.0000'), sale_price=Decimal('100.0000'),
        )
        cls.customer = Party.objects.create(code='FIN-CUST', name='FIN Customer', is_customer=True, is_supplier=True)
        Stock.objects.create(warehouse=cls.wh, product=cls.product, quantity=Decimal('100'))
        Stock.objects.create(warehouse=cls.other_wh, product=cls.product, quantity=Decimal('50'))

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.admin)

    def _bal(self, acct):
        return accounts_with_balances(FinancialAccount.objects.filter(pk=acct.pk)).get().balance

    def _make_sale(self, qty='10', price='100', mode=SettlementMode.DEFERRED, wh=None, account=None):
        order = SalesOrder.objects.create(
            customer=self.customer, warehouse=wh or self.wh, payment_account=account,
            currency_id='BDT', exchange_rate=Decimal('1'), settlement_mode=mode,
        )
        item = SalesOrderItem.objects.create(
            sales_order=order, line_number=1, product=self.product, unit=self.unit,
            quantity=Decimal(qty), unit_price=Decimal(price),
        )
        return order, item

    def test_f_fin_1_and_2_credit_sale_partial_collect_and_return_credit_offset(self):
        order, item = self._make_sale(qty='10', price='100', mode=SettlementMode.DEFERRED)
        stock_before = Stock.objects.get(warehouse=self.wh, product=self.product).quantity
        cash_before = self._bal(self.cash)

        complete_sale(self.admin, order.pk, settlement_mode=SettlementMode.DEFERRED)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.DUE)
        self.assertEqual(Stock.objects.get(warehouse=self.wh, product=self.product).quantity, stock_before - 10)
        self.assertEqual(self._bal(self.cash), cash_before)

        # F-FIN-1: Collect 400 -> open_due=600, status=partial
        url = reverse('accounting:sales-payment-list', args=[order.pk])
        key1 = str(uuid4())
        payload = {
            'request_key': key1,
            'payment_date': timezone.localdate().isoformat(),
            'tenders': [{'method': 'cash', 'account': self.cash.pk, 'amount': '400.00'}],
        }
        resp = self.client.post(url, payload, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['due'], '600.0000')
        self.assertEqual(resp.data['payment_status'], 'partial')
        self.assertEqual(self._bal(self.cash), cash_before + Decimal('400.0000'))

        # Idempotent retry -> same result, no extra cash
        retry = self.client.post(url, payload, format='json')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data, resp.data)
        self.assertEqual(self._bal(self.cash), cash_before + Decimal('400.0000'))

        # Same key with different payload -> 409
        conflict = self.client.post(url, {**payload, 'tenders': [{'method': 'cash', 'account': self.cash.pk, 'amount': '100.00'}]}, format='json')
        self.assertEqual(conflict.status_code, 409)

        # Overpay -> 400
        overpay = self.client.post(url, {
            'request_key': str(uuid4()),
            'payment_date': timezone.localdate().isoformat(),
            'tenders': [{'method': 'cash', 'account': self.cash.pk, 'amount': '601.00'}],
        }, format='json')
        self.assertEqual(overpay.status_code, 400)

        # Zero-rounding amount -> 400
        tiny = self.client.post(url, {
            'request_key': str(uuid4()),
            'payment_date': timezone.localdate().isoformat(),
            'tenders': [{'method': 'cash', 'account': self.cash.pk, 'amount': '0.001'}],
        }, format='json')
        self.assertEqual(tiny.status_code, 400)

        # F-FIN-2: Return 2.5 units (value 250) -> open_due=350, NO cash refund
        ret = SalesReturn.objects.create(sales_order=order)
        SalesReturnItem.objects.create(sales_return=ret, sales_order_item=item, quantity=Decimal('2.5'))
        complete_sale_return(self.admin, ret.pk)
        order.refresh_from_db()
        bal = compute_order_balance(order)
        self.assertEqual(bal.open_due, Decimal('350.0000'))
        self.assertEqual(order.payment_status, PaymentStatus.PARTIAL)
        self.assertEqual(self._bal(self.cash), cash_before + Decimal('400.0000'))
        self.assertFalse(SalesPayment.objects.filter(sales_return=ret).exists())

    def test_f_fin_3_and_4_split_tender_and_paid_refund(self):
        order, item = self._make_sale(qty='10', price='100', mode=SettlementMode.EXPLICIT)
        cash_before = self._bal(self.cash)
        bank_before = self._bal(self.card_bank)

        # F-FIN-4: Explicit split 400 cash + 600 card at completion
        complete_sale(
            self.admin, order.pk,
            settlement_mode=SettlementMode.EXPLICIT,
            tenders=[
                {'method': 'cash', 'account': self.cash.pk, 'amount': '400.00'},
                {'method': 'card', 'account': self.card_bank.pk, 'amount': '600.00', 'card_last4': '4242'},
            ],
            request_key=uuid4(),
        )
        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.PAID)
        self.assertEqual(order.payments.count(), 2)
        self.assertEqual(self._bal(self.cash), cash_before + Decimal('400.0000'))
        self.assertEqual(self._bal(self.card_bank), bank_before + Decimal('600.0000'))

        # F-FIN-3: Return 2.5 units (250) -> refunded LIFO (card row), net collections 750, due 0, status paid
        ret = SalesReturn.objects.create(sales_order=order)
        SalesReturnItem.objects.create(sales_return=ret, sales_order_item=item, quantity=Decimal('2.5'))
        complete_sale_return(self.admin, ret.pk)
        bal = compute_order_balance(order)
        self.assertEqual(bal.open_due, Decimal('0.0000'))
        self.assertEqual(bal.net_paid, Decimal('750.0000'))
        self.assertEqual(bal.payment_status, PaymentStatus.PAID)
        refund_row = SalesPayment.objects.get(sales_return=ret)
        self.assertIsNotNone(refund_row.refund_of)
        self.assertEqual(refund_row.method, 'card')

    def test_f_fin_5_purchase_multi_installment_return_and_reversal(self):
        po = PurchaseOrder.objects.create(
            supplier=self.customer, warehouse=self.wh, currency_id='BDT', exchange_rate=Decimal('1'),
            settlement_mode=SettlementMode.DEFERRED,
        )
        item = PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=self.product, unit=self.unit,
            quantity=Decimal('10'), unit_price=Decimal('100.0000'),
        )
        receive_purchase(self.admin, po.pk, settlement_mode=SettlementMode.DEFERRED)
        po.refresh_from_db()
        self.assertEqual(po.payment_status, PaymentStatus.DUE)

        url = reverse('accounting:purchase-payment-list', args=[po.pk])
        r1 = self.client.post(url, {
            'request_key': str(uuid4()), 'payment_date': timezone.localdate().isoformat(),
            'tenders': [{'method': 'cash', 'account': self.cash.pk, 'amount': '400.00'}],
        }, format='json')
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.data['due'], '600.0000')

        # 2nd installment accepted
        r2 = self.client.post(url, {
            'request_key': str(uuid4()), 'payment_date': timezone.localdate().isoformat(),
            'tenders': [{'method': 'bank_transfer', 'account': self.card_bank.pk, 'amount': '200.00'}],
        }, format='json')
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data['due'], '400.0000')
        self.assertEqual(PurchasePayment.objects.filter(purchase_order=po).count(), 2)

        # Reverse the 2nd installment -> due returns to 600
        rev_url = reverse('accounting:purchase-payment-reverse', args=[r2.data['payment_ids'][0]])
        rev = self.client.post(rev_url, {'request_key': str(uuid4()), 'reason': 'Wrong bank'}, format='json')
        self.assertEqual(rev.status_code, 200, rev.data)
        self.assertEqual(rev.data['due'], '600.0000')

        # Return 2.5 units (250) -> due 350
        pret = PurchaseReturn.objects.create(purchase_order=po)
        PurchaseReturnItem.objects.create(purchase_return=pret, purchase_order_item=item, quantity=Decimal('2.5'))
        complete_purchase_return(self.admin, pret.pk)
        bal = compute_order_balance(po)
        self.assertEqual(bal.open_due, Decimal('350.0000'))
        self.assertEqual(bal.payment_status, PaymentStatus.PARTIAL)

    def test_clear_due_and_dual_role_summary_and_warehouse_scope(self):
        s1, _ = self._make_sale(qty='2', price='100', mode=SettlementMode.DEFERRED)
        s2, _ = self._make_sale(qty='3', price='100', mode=SettlementMode.DEFERRED)
        complete_sale(self.admin, s1.pk, settlement_mode=SettlementMode.DEFERRED)
        complete_sale(self.admin, s2.pk, settlement_mode=SettlementMode.DEFERRED)

        # Supplier purchase does not inflate customer receivable
        po = PurchaseOrder.objects.create(
            supplier=self.customer, warehouse=self.wh, currency_id='BDT', exchange_rate=Decimal('1'),
            settlement_mode=SettlementMode.DEFERRED,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=self.product, unit=self.unit,
            quantity=Decimal('4'), unit_price=Decimal('100.0000'),
        )
        receive_purchase(self.admin, po.pk, settlement_mode=SettlementMode.DEFERRED)

        summary = self.client.get(reverse('accounting:party-due-summary', args=[self.customer.pk])).data
        self.assertEqual(summary['receivable']['totals']['due'], '500.0000')
        self.assertEqual(summary['payable']['totals']['due'], '400.0000')

        # Clear 250 receivable -> 200 to s1 (oldest), 50 to s2
        prev = self.client.post(
            reverse('accounting:party-clear-due-preview', args=[self.customer.pk]),
            {'amount': '250.00', 'direction': 'receipt'}, format='json',
        )
        self.assertEqual(prev.status_code, 200)
        self.assertEqual([a['allocated'] for a in prev.data['allocations']], ['200.0000', '50.0000'])

        conf = self.client.post(
            reverse('accounting:party-clear-due-confirm', args=[self.customer.pk]),
            {
                'amount': '250.00', 'direction': 'receipt', 'account': self.cash.pk,
                'method': 'cash', 'payment_date': timezone.localdate().isoformat(),
                'request_key': str(uuid4()),
            },
            format='json',
        )
        self.assertEqual(conf.status_code, 200, conf.data)
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.payment_status, PaymentStatus.PAID)
        self.assertEqual(s2.payment_status, PaymentStatus.PARTIAL)

        # Out-of-scope warehouse order -> 404
        remote_sale, _ = self._make_sale(qty='1', price='100', mode=SettlementMode.DEFERRED, wh=self.other_wh)
        complete_sale(self.admin, remote_sale.pk, settlement_mode=SettlementMode.DEFERRED)
        self.client.force_login(self.branch_user)
        resp = self.client.post(
            reverse('accounting:sales-payment-list', args=[remote_sale.pk]),
            {'request_key': str(uuid4()), 'payment_date': timezone.localdate().isoformat(),
             'tenders': [{'method': 'cash', 'account': self.remote_cash.pk, 'amount': '50.00'}]},
            format='json',
        )
        self.assertEqual(resp.status_code, 404)


class Fin01ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='fin-race-admin')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        self.wh = WareHouse.objects.create(name='Race WH', address='Dhaka', phone='01')
        self.cash = FinancialAccount.objects.create(
            name='Race Cash', type=FinancialAccount.Type.CASH, warehouse=self.wh, opening_balance=Decimal('1000'),
        )
        unit = UnitOfMeasure.objects.create(name='Pieces', code='RACE-FIN-PCS')
        product = Product.objects.create(name='Race Item', sku='RACE-FIN', unit=unit, currency_id='BDT')
        Stock.objects.create(warehouse=self.wh, product=product, quantity=Decimal('10'))
        self.order = SalesOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=Decimal('1'),
            settlement_mode=SettlementMode.DEFERRED,
        )
        SalesOrderItem.objects.create(
            sales_order=self.order, line_number=1, product=product, unit=unit,
            quantity=Decimal('1'), unit_price=Decimal('100.0000'),
        )
        complete_sale(self.admin, self.order.pk, settlement_mode=SettlementMode.DEFERRED)

    def test_concurrent_payments_on_last_100_due_exactly_one_succeeds(self):
        from time import sleep
        from accounting.services import post_sales_payment

        barrier = Barrier(2)
        results = []

        def worker():
            close_old_connections()
            actor = get_user_model().objects.get(pk=self.admin.pk)
            barrier.wait(timeout=10)
            for _ in range(30):
                try:
                    res = post_sales_payment(
                        actor, self.order.pk,
                        [{'method': 'cash', 'account_id': self.cash.pk, 'amount': '100.00'}],
                        timezone.localdate(), uuid4(),
                    )
                    results.append(('ok', res))
                    return
                except Exception as exc:
                    if 'locked' in str(exc).lower():
                        sleep(0.02)
                        continue
                    results.append(('err', str(exc)))
                    return
            results.append(('err', 'timeout'))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker), pool.submit(worker)]
            for f in futures:
                f.result()

        oks = [r for r in results if r[0] == 'ok']
        errs = [r for r in results if r[0] == 'err']
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(len(errs), 1, results)
        self.assertEqual(compute_order_balance(self.order).open_due, Decimal('0.0000'))
