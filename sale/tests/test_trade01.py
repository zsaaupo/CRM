from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import sleep
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import FinancialAccount
from accounting.services import compute_order_balance
from common.models import DiscountType, PaymentStatus, ProductStatus, SettlementMode, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from people.models import Biller, BillerWarehouse, Party
from purchase.models import PurchaseOrder, PurchaseOrderItem, PurchaseReturn, PurchaseReturnItem
from purchase.returns import complete_return as complete_purchase_return
from purchase.services import receive_purchase
from sale.models import SalesOrder, SalesOrderItem, SalesReturn, SalesReturnItem
from sale.returns import complete_return as complete_sale_return
from sale.services import complete_sale
from warehouse.models import Stock, WareHouse


class Trade01Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='trd01-admin')
        cls.wh = WareHouse.objects.create(name='TRD Main', address='Dhaka', phone='017')
        cls.cash = FinancialAccount.objects.create(
            name='TRD Cash', type=FinancialAccount.Type.CASH, warehouse=cls.wh,
            opening_balance=Decimal('10000.0000'),
        )
        cls.unit = UnitOfMeasure.objects.create(name='Pieces', code='TRD01-PCS')
        cls.prod_a = Product.objects.create(name='Prod A', sku='TRD-A', unit=cls.unit, currency_id='BDT')
        cls.prod_b = Product.objects.create(name='Prod B', sku='TRD-B', unit=cls.unit, currency_id='BDT')
        cls.supplier = Party.objects.create(code='TRD-SUP', name='TRD Supplier', is_supplier=True)
        cls.customer = Party.objects.create(code='TRD-CUS', name='TRD Customer', is_customer=True)
        cls.biller = Biller.objects.create(name='Original Biller')
        BillerWarehouse.objects.create(biller=cls.biller, warehouse=cls.wh)
        Stock.objects.create(warehouse=cls.wh, product=cls.prod_a, quantity=Decimal('50'))
        Stock.objects.create(warehouse=cls.wh, product=cls.prod_b, quantity=Decimal('50'))

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.admin)

    def test_f_trd_1_partial_receipts_independent_payment_and_return_cap(self):
        po = PurchaseOrder.objects.create(
            supplier=self.supplier, warehouse=self.wh, currency_id='BDT', exchange_rate=Decimal('1'),
            settlement_mode=SettlementMode.DEFERRED,
        )
        line = PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=self.prod_a, unit=self.unit,
            quantity=Decimal('10'), unit_price=Decimal('100.0000'),
        )
        rec_url = reverse('purchase:purchase-order-receipts', args=[po.pk])
        pay_url = reverse('accounting:purchase-payment-list', args=[po.pk])

        # Receive 4 -> recognized 400
        r1 = self.client.post(rec_url, {
            'request_key': str(uuid4()), 'receipt_date': timezone.localdate().isoformat(),
            'lines': [{'order_line': line.pk, 'quantity': '4'}],
            'settlement_mode': 'deferred',
        }, format='json')
        self.assertEqual(r1.status_code, 200, r1.data)
        self.assertEqual(r1.data['receipt_state'], 'partial')
        po.refresh_from_db()
        self.assertEqual(po.recognized_total_base, Decimal('400.0000'))
        self.assertEqual(po.payment_status, PaymentStatus.DUE)

        # Returning 5 after receiving only 4 fails
        ret_over = PurchaseReturn.objects.create(purchase_order=po)
        PurchaseReturnItem.objects.create(purchase_return=ret_over, purchase_order_item=line, quantity=Decimal('5'))
        with self.assertRaises(Exception):
            complete_purchase_return(self.admin, ret_over.pk)

        # Pay 300 -> due 100 (partial)
        p1 = self.client.post(pay_url, {
            'request_key': str(uuid4()), 'payment_date': timezone.localdate().isoformat(),
            'tenders': [{'method': 'cash', 'account': self.cash.pk, 'amount': '300.00'}],
        }, format='json')
        self.assertEqual(p1.status_code, 200, p1.data)
        self.assertEqual(p1.data['due'], '100.0000')
        self.assertEqual(p1.data['payment_status'], 'partial')

        # Receive remaining 6 -> recognized 1000, due 700
        r2 = self.client.post(rec_url, {
            'request_key': str(uuid4()), 'receipt_date': timezone.localdate().isoformat(),
            'lines': [{'order_line': line.pk, 'quantity': '6'}],
            'settlement_mode': 'deferred',
        }, format='json')
        self.assertEqual(r2.status_code, 200, r2.data)
        self.assertEqual(r2.data['receipt_state'], 'received')
        po.refresh_from_db()
        self.assertEqual(compute_order_balance(po).open_due, Decimal('700.0000'))

        # Return 2 -> credit 200 -> due 500; stock = 50 + 10 - 2 = 58
        ret2 = PurchaseReturn.objects.create(purchase_order=po)
        PurchaseReturnItem.objects.create(purchase_return=ret2, purchase_order_item=line, quantity=Decimal('2'))
        complete_purchase_return(self.admin, ret2.pk)
        po.refresh_from_db()
        self.assertEqual(compute_order_balance(po).open_due, Decimal('500.0000'))
        self.assertEqual(Stock.objects.get(warehouse=self.wh, product=self.prod_a).quantity, Decimal('58'))

    def test_f_trd_2_header_discount_allocation_and_cumulative_return(self):
        order = SalesOrder.objects.create(
            customer=self.customer, warehouse=self.wh, payment_account=self.cash,
            currency_id='BDT', exchange_rate=Decimal('1'),
            order_discount_type=DiscountType.PERCENTAGE, order_discount_value=Decimal('10.0000'),
            biller=self.biller,
        )
        la = SalesOrderItem.objects.create(
            sales_order=order, line_number=1, product=self.prod_a, unit=self.unit,
            quantity=Decimal('3'), unit_price=Decimal('100.0000'), discount_amount=Decimal('30.0000'),
        )
        lb = SalesOrderItem.objects.create(
            sales_order=order, line_number=2, product=self.prod_b, unit=self.unit,
            quantity=Decimal('2'), unit_price=Decimal('50.0000'),
        )
        complete_sale(self.admin, order.pk)
        order.refresh_from_db()
        la.refresh_from_db()
        lb.refresh_from_db()

        self.assertEqual(order.order_discount_amount, Decimal('37.0000'))
        self.assertEqual(la.allocated_order_discount, Decimal('27.0000'))
        self.assertEqual(lb.allocated_order_discount, Decimal('10.0000'))
        self.assertEqual(order.grand_total, Decimal('333.0000'))
        self.assertEqual(order.biller_name_snapshot, 'Original Biller')

        # Biller rename does not change posted biller_name_snapshot
        self.biller.name = 'Renamed Biller'
        self.biller.save()
        order.refresh_from_db()
        self.assertEqual(order.biller_name_snapshot, 'Original Biller')

        # Return 1: A x 1 (81) + B x 1 (45) = 126
        r1 = SalesReturn.objects.create(sales_order=order)
        SalesReturnItem.objects.create(sales_return=r1, sales_order_item=la, quantity=Decimal('1'))
        SalesReturnItem.objects.create(sales_return=r1, sales_order_item=lb, quantity=Decimal('1'))
        complete_sale_return(self.admin, r1.pk)
        ref1 = sum(r1.refund_payments.values_list('amount', flat=True))
        self.assertEqual(ref1, Decimal('126.0000'))

        # Return 2: A x 2 (162) + B x 1 (45) = 207 -> cumulative = 333
        r2 = SalesReturn.objects.create(sales_order=order)
        SalesReturnItem.objects.create(sales_return=r2, sales_order_item=la, quantity=Decimal('2'))
        SalesReturnItem.objects.create(sales_return=r2, sales_order_item=lb, quantity=Decimal('1'))
        complete_sale_return(self.admin, r2.pk)
        ref2 = sum(r2.refund_payments.values_list('amount', flat=True))
        self.assertEqual(ref2, Decimal('207.0000'))
        self.assertEqual(ref1 + ref2, Decimal('333.0000'))

    def test_duplicate_orders_create_fresh_unposted_drafts(self):
        sale = SalesOrder.objects.create(
            customer=self.customer, warehouse=self.wh, payment_account=self.cash,
            currency_id='BDT', exchange_rate=Decimal('1'), notes='Repeat me',
        )
        SalesOrderItem.objects.create(
            sales_order=sale, line_number=1, product=self.prod_a, unit=self.unit,
            quantity=Decimal('2'), unit_price=Decimal('50.0000'),
        )
        complete_sale(self.admin, sale.pk)
        dup_s = self.client.post(reverse('sale:sales-order-duplicate', args=[sale.pk]))
        self.assertEqual(dup_s.status_code, 201, dup_s.data)
        cloned_sale = SalesOrder.objects.get(pk=dup_s.data['id'])
        self.assertFalse(cloned_sale.is_completed)
        self.assertEqual(cloned_sale.payment_status, PaymentStatus.PENDING)
        self.assertNotEqual(cloned_sale.order_number, sale.order_number)
        self.assertEqual(cloned_sale.items.count(), 1)
        self.assertFalse(cloned_sale.payments.exists())

        po = PurchaseOrder.objects.create(
            supplier=self.supplier, warehouse=self.wh, payment_account=self.cash,
            currency_id='BDT', exchange_rate=Decimal('1'),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po, line_number=1, product=self.prod_a, unit=self.unit,
            quantity=Decimal('2'), unit_price=Decimal('50.0000'),
        )
        receive_purchase(self.admin, po.pk)
        dup_p = self.client.post(reverse('purchase:purchase-order-duplicate', args=[po.pk]))
        self.assertEqual(dup_p.status_code, 201, dup_p.data)
        cloned_po = PurchaseOrder.objects.get(pk=dup_p.data['id'])
        self.assertFalse(cloned_po.is_received)
        self.assertEqual(cloned_po.items.count(), 1)


class Trade01ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='trd-race-admin')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        self.wh = WareHouse.objects.create(name='TRD Race WH', address='Dhaka', phone='01')
        unit = UnitOfMeasure.objects.create(name='Pieces', code='TRD-RACE-PCS')
        prod = Product.objects.create(name='TRD Race', sku='TRD-RACE', unit=unit, currency_id='BDT')
        self.po = PurchaseOrder.objects.create(
            warehouse=self.wh, currency_id='BDT', exchange_rate=1, settlement_mode=SettlementMode.DEFERRED,
        )
        self.line = PurchaseOrderItem.objects.create(
            purchase_order=self.po, line_number=1, product=prod, unit=unit, quantity=Decimal('1'), unit_price=Decimal('10'),
        )

    def test_two_receipts_racing_on_last_unit_one_wins(self):
        barrier = Barrier(2)
        results = []

        def worker():
            close_old_connections()
            actor = get_user_model().objects.get(pk=self.admin.pk)
            barrier.wait(timeout=10)
            for _ in range(30):
                try:
                    res = receive_purchase(
                        actor, self.po.pk,
                        receipts=[{'order_line': self.line.pk, 'quantity': '1'}],
                        receipt_date=timezone.localdate(),
                        request_key=uuid4(),
                        settlement_mode=SettlementMode.DEFERRED,
                    )
                    results.append(('ok', res))
                    return
                except Exception as exc:
                    if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                        sleep(0.02)
                        continue
                    results.append(('err', str(exc)))
                    return
            results.append(('err', 'timeout'))

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(worker)
            f2 = pool.submit(worker)
            f1.result()
            f2.result()

        oks = [r for r in results if r[0] == 'ok']
        errs = [r for r in results if r[0] == 'err']
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(len(errs), 1, results)
        self.line.refresh_from_db()
        self.assertEqual(self.line.received_quantity, Decimal('1'))
