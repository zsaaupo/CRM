from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from threading import Barrier
from time import sleep
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from rest_framework.test import APIClient

from accounting.models import AccountTransaction, DepositEntry, FinancialAccount, PaymentMethod, SalesPayment
from common.models import GeneralSetting, ProductStatus, SettlementMode, TradeStatus
from common.settings_store import get_general, invalidate_general_cache
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from people.models import Biller, BillerWarehouse, Party
from purchase.models import PurchaseOrder
from sale.loyalty import add_deposit, issue_gift_card
from sale.models import (
    CashRegisterSession, Challan, Coupon, CouponRedemption, Courier, Delivery,
    Discount, DiscountPlan, GiftCard, PackingSlip, QuotationConversion, SalesOrder,
    SalesOrderItem, SalesQuotation, SalesQuotationItem, SalesReturn, SalesReturnItem,
)
from sale.pos import close_register, open_register, pos_checkout, register_summary
from sale.returns import complete_return as complete_sale_return
from warehouse.models import RoleWarehouseAccess, Stock, StockMovement, WareHouse
from warehouse.opening import post_opening_stock


class M3FunctionalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='m3-admin', password='pw')
        cls.wh = WareHouse.objects.create(name='M3 WH', address='Dhaka', phone='01')
        cls.cash = FinancialAccount.objects.create(
            name='M3 Cash', type=FinancialAccount.Type.CASH, warehouse=cls.wh, opening_balance=10000,
        )
        cls.card = FinancialAccount.objects.create(
            name='M3 Card', type=FinancialAccount.Type.BANK, warehouse=cls.wh, opening_balance=10000,
        )
        cls.pcs = UnitOfMeasure.objects.create(name='Piece', code='M3-PCS')
        cls.prod = Product.objects.create(
            sku='M3-P1', name='উইজেট পণ্য Long Widget Name', unit=cls.pcs,
            currency_id='BDT', cost_price=Decimal('60'), sale_price=Decimal('1000'),
            is_featured=True, barcode='M3-BAR-1',
        )
        cls.cheap = Product.objects.create(
            sku='M3-P2', name='Budget Item', unit=cls.pcs,
            currency_id='BDT', cost_price=Decimal('40'), sale_price=Decimal('100'),
        )
        cls.cust = Party.objects.create(name='M3 Cust', code='M3-CUST', is_customer=True, phone='+8801711111111', address='Dhaka')
        cls.supp = Party.objects.create(name='M3 Supp', code='M3-SUPP', is_supplier=True)
        cls.biller = Biller.objects.create(name='M3 Desk')
        BillerWarehouse.objects.create(biller=cls.biller, warehouse=cls.wh)
        post_opening_stock(
            cls.admin,
            [
                {'warehouse': cls.wh.pk, 'product': cls.prod.pk, 'quantity': '20', 'unit_cost_base': '60'},
                {'warehouse': cls.wh.pk, 'product': cls.cheap.pk, 'quantity': '20', 'unit_cost_base': '40'},
            ],
            uuid4(),
        )

    def test_quo01_quotation_to_purchase_conversion(self):
        client = APIClient()
        client.force_login(self.admin)

        q = SalesQuotation.objects.create(
            customer=self.cust, supplier=self.supp, biller=self.biller,
            warehouse=self.wh, currency_id='BDT', exchange_rate=1,
        )
        SalesQuotationItem.objects.create(
            quotation=q, line_number=1, product=self.cheap, unit=self.pcs,
            quantity=Decimal('5'), unit_price=Decimal('100'),
        )
        # Customer-only party rejected as supplier
        prev_bad = client.get(
            reverse('sale:quotation-convert-purchase-preview', args=[q.pk]),
            {'supplier': self.cust.pk, 'cost_basis': 'product_cost'},
        )
        self.assertEqual(prev_bad.status_code, 400)

        prev_ok = client.get(
            reverse('sale:quotation-convert-purchase-preview', args=[q.pk]),
            {'supplier': self.supp.pk, 'cost_basis': 'product_cost'},
        )
        self.assertEqual(prev_ok.status_code, 200)
        self.assertEqual(prev_ok.data['lines'][0]['unit_price'], '40.0000')

        key = str(uuid4())
        conv1 = client.post(
            reverse('sale:quotation-convert-purchase', args=[q.pk]),
            {'supplier': self.supp.pk, 'cost_basis': 'quoted_price', 'request_key': key},
            format='json',
        )
        self.assertEqual(conv1.status_code, 200, conv1.data)
        po_id = conv1.data['purchase_order_id']
        po = PurchaseOrder.objects.get(pk=po_id)
        self.assertFalse(po.is_received)
        self.assertEqual(po.items.get().unit_price, Decimal('100.0000'))

        # Retry with new key still hits single QuotationConversion per revision+kind
        conv2 = client.post(
            reverse('sale:quotation-convert-purchase', args=[q.pk]),
            {'supplier': self.supp.pk, 'cost_basis': 'quoted_price', 'request_key': str(uuid4())},
            format='json',
        )
        self.assertEqual(conv2.status_code, 200)
        self.assertEqual(conv2.data['purchase_order_id'], po_id)
        self.assertEqual(QuotationConversion.objects.filter(quotation_revision=q, kind='purchase').count(), 1)

    def test_f_pos_1_register_mixed_tenders_refund_and_thermal_receipts(self):
        client = APIClient()
        client.force_login(self.admin)

        self.assertEqual(client.get(reverse('pos')).status_code, 200)
        reg_resp = client.post(
            reverse('sale:register-open'),
            {'warehouse': self.wh.pk, 'opening_cash': '500', 'request_key': str(uuid4())},
            format='json',
        )
        self.assertEqual(reg_resp.status_code, 201, reg_resp.data)
        reg_id = reg_resp.data['id']

        # Hold draft creates zero stock movements
        draft = client.post(reverse('sale:pos-draft-list'), {'warehouse': self.wh.pk, 'cart': {'items': []}}, format='json')
        self.assertEqual(draft.status_code, 201)
        self.assertFalse(StockMovement.objects.filter(kind=StockMovement.Kind.SALE_ISSUE).exists())

        # F-POS-1 checkout: 1,000 (400 cash + 600 card)
        chk_key = str(uuid4())
        chk = client.post(
            reverse('sale:pos-checkout'),
            {
                'request_key': chk_key,
                'register': reg_id,
                'warehouse': self.wh.pk,
                'customer': self.cust.pk,
                'biller': self.biller.pk,
                'lines': [{'product': self.prod.pk, 'quantity': '1', 'unit': self.pcs.pk}],
                'tenders': [
                    {'method': 'card', 'account': self.card.pk, 'amount': '600.00'},
                    {'method': 'cash', 'account': self.cash.pk, 'amount': '400.00'},
                ],
            },
            format='json',
        )
        self.assertEqual(chk.status_code, 200, chk.data)
        so_id = chk.data['sales_order_id']
        self.assertEqual(chk.data['grand_total'], '1000.0000')

        # Replay same checkout key -> zero extra stock or payments
        replay = client.post(
            reverse('sale:pos-checkout'),
            {
                'request_key': chk_key,
                'register': reg_id,
                'warehouse': self.wh.pk,
                'customer': self.cust.pk,
                'biller': self.biller.pk,
                'lines': [{'product': self.prod.pk, 'quantity': '1', 'unit': self.pcs.pk}],
                'tenders': [
                    {'method': 'card', 'account': self.card.pk, 'amount': '600.00'},
                    {'method': 'cash', 'account': self.cash.pk, 'amount': '400.00'},
                ],
            },
            format='json',
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data['idempotent_replay'])
        self.assertEqual(Stock.objects.get(warehouse=self.wh, product=self.prod).quantity, Decimal('19'))

        # Cash refund 100 (partial return of 0.1) -> expected drawer 500 + 400 - 100 = 800
        so = SalesOrder.objects.get(pk=so_id)
        so_item = so.items.get()
        sr = SalesReturn.objects.create(sales_order=so)
        SalesReturnItem.objects.create(sales_return=sr, sales_order_item=so_item, quantity=Decimal('0.1'))
        complete_sale_return(self.admin, sr.pk)
        summary = client.get(reverse('sale:register-summary', args=[reg_id])).data
        self.assertEqual(summary['expected_cash'], '800.0000')

        closed = client.post(
            reverse('sale:register-close', args=[reg_id]),
            {'counted_cash': '800', 'request_key': str(uuid4())},
            format='json',
        )
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.data['variance'], '0.0000')

        # Thermal receipts A4, 58mm, 80mm all render valid PDFs
        for fmt in ('a4', '58mm', '80mm'):
            pdf = client.get(reverse('report:pos-receipt-pdf', args=[so_id]), {'format': fmt})
            self.assertEqual(pdf.status_code, 200)
            self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_f_prc_1_2_gift_card_deposit_and_discount_plan(self):
        client = APIClient()
        client.force_login(self.admin)

        # Open register
        reg, _ = open_register(self.admin, self.wh.pk, Decimal('0'), uuid4())

        # DiscountPlan 10% for cust on cheap product today only
        today = timezone.localdate()
        plan = DiscountPlan.objects.create(name='VIP Plan')
        plan.customers.add(self.cust)
        Discount.objects.create(
            plan=plan, name='Today 10%', valid_from=today, valid_till=today,
            discount_type='percentage', value=Decimal('10'), minimum_qty=Decimal('1'),
        )

        # F-PRC-1: Gift card 500 -> spend 200 -> balance 300; duplicate key ignored
        gc, _ = issue_gift_card(self.admin, 'GC-500', Decimal('500'), customer=self.cust, account_id=self.cash.pk, request_key=uuid4())
        # Expired card
        gc_exp, _ = issue_gift_card(
            self.admin, 'GC-EXP', Decimal('100'), expired_date=today - timedelta(days=1), request_key=uuid4(),
        )
        # F-PRC-2: Deposit 500 -> apply 180 -> balance 320, zero extra cash
        add_deposit(self.admin, self.cust.pk, Decimal('500'), self.cash.pk, 'Advance deposit', uuid4())
        cash_tx_before = AccountTransaction.objects.filter(account=self.cash).count()

        # Sell 2 x cheap (100 - 10% plan = 90 each -> 180) + 2 x cheap (total 360) OR buy 4 x 90 = 360:
        # pay 200 gift card + 160? Even exact F-PRC-1/F-PRC-2: 4.222 -> let's use manual_price=None with 10% off 100 = 90:
        # 2x90=180 via deposit OR 180 deposit + 200 gift card = 380! Wait: let's sell qty 4 of cheap (4 * 90 = 360, or add coupon / manual)
        # Let's check exact 180 deposit + 200 gift card = 380: qty 3.8? Better: qty 2 @ 90 + manual 190? Just qty=1 of prod?
        # Even cleaner: F-PRC-1 check 200 gift card on order=200 -> card balance 300; F-PRC-2 check 180 deposit on order=180 -> deposit 320!
        chk1 = pos_checkout(
            self.admin,
            {
                'register': reg.pk, 'warehouse': self.wh.pk, 'customer': self.cust.pk,
                'lines': [{'product': self.cheap.pk, 'quantity': '2', 'unit': self.pcs.pk}],  # 2 * 90 = 180 via DiscountPlan!
                'tenders': [{'method': 'deposit', 'amount': '180.00'}],
            },
            uuid4(),
        )[0]
        self.cust.refresh_from_db()
        self.assertEqual(self.cust.deposit_balance, Decimal('320.0000'))
        self.assertEqual(AccountTransaction.objects.filter(account=self.cash).count(), cash_tx_before)  # ZERO cash inflow on deposit redeem!
        self.assertEqual(chk1['grand_total'], '180.0000')

        # Gift card spend 200 -> 300
        pos_checkout(
            self.admin,
            {
                'register': reg.pk, 'warehouse': self.wh.pk, 'customer': self.cust.pk,
                'lines': [{'product': self.cheap.pk, 'quantity': '2', 'unit': self.pcs.pk, 'manual_price': '100'}],
                'tenders': [{'method': 'gift_card', 'gift_card_id': gc.pk, 'amount': '200.00'}],
            },
            uuid4(),
        )
        gc.refresh_from_db()
        self.assertEqual(gc.balance, Decimal('300.0000'))

        # Expired gift card rejected cleanly
        with self.assertRaises(Exception):
            pos_checkout(
                self.admin,
                {
                    'register': reg.pk, 'warehouse': self.wh.pk,
                    'lines': [{'product': self.cheap.pk, 'quantity': '1', 'unit': self.pcs.pk}],
                    'tenders': [{'method': 'gift_card', 'gift_card_id': gc_exp.pk, 'amount': '50.00'}],
                },
                uuid4(),
            )

    def test_ship01_delivery_packing_slip_challan_and_whatsapp(self):
        client = APIClient()
        client.force_login(self.admin)

        invalidate_general_cache()
        gs = get_general()
        gs.enable_packing_slip = True
        gs.save()
        invalidate_general_cache()

        reg, _ = open_register(self.admin, self.wh.pk, Decimal('0'), uuid4())
        so_res, _ = pos_checkout(
            self.admin,
            {
                'register': reg.pk, 'warehouse': self.wh.pk, 'customer': self.cust.pk,
                'lines': [{'product': self.prod.pk, 'quantity': '3', 'unit': self.pcs.pk}],
                'tenders': [{'method': 'cash', 'account': self.cash.pk, 'amount': '3000'}],
            },
            uuid4(),
        )
        so = SalesOrder.objects.get(pk=so_res['sales_order_id'])
        line = so.items.get()

        courier = Courier.objects.create(name='Steadfast', phone='01800000000')
        # Exceeding sold qty rejected
        bad_deliv = client.post(
            reverse('sale:delivery-list'),
            {'sales_order': so.pk, 'courier': courier.pk, 'items': [{'sales_order_item': line.pk, 'quantity': '4'}]},
            format='json',
        )
        self.assertEqual(bad_deliv.status_code, 400)

        deliv = client.post(
            reverse('sale:delivery-list'),
            {'sales_order': so.pk, 'courier': courier.pk, 'items': [{'sales_order_item': line.pk, 'quantity': '2'}]},
            format='json',
        )
        self.assertEqual(deliv.status_code, 201, deliv.data)

        slip1 = client.post(
            reverse('sale:packing-slip-list'),
            {'sales_order': so.pk, 'delivery': deliv.data['id'], 'items': [{'sales_order_item': line.pk, 'quantity': '2'}]},
            format='json',
        )
        slip2 = client.post(
            reverse('sale:packing-slip-list'),
            {'sales_order': so.pk, 'items': [{'sales_order_item': line.pk, 'quantity': '1'}]},
            format='json',
        )
        self.assertEqual((slip1.status_code, slip2.status_code), (201, 201))

        # Two slips on one challan
        ch1 = client.post(
            reverse('sale:challan-list'),
            {'courier': courier.pk, 'packing_slips': [slip1.data['id'], slip2.data['id']], 'amount': '120'},
            format='json',
        )
        self.assertEqual(ch1.status_code, 201, ch1.data)

        # Cannot place slip1 on a second open challan
        ch_dup = client.post(
            reverse('sale:challan-list'),
            {'courier': courier.pk, 'packing_slips': [slip1.data['id']]},
            format='json',
        )
        self.assertEqual(ch_dup.status_code, 409)

        # Close challan idempotently
        c_key = str(uuid4())
        cl1 = client.post(reverse('sale:challan-close', args=[ch1.data['id']]), {'request_key': c_key, 'closing_date': '2026-06-01'}, format='json')
        cl2 = client.post(reverse('sale:challan-close', args=[ch1.data['id']]), {'request_key': str(uuid4()), 'closing_date': '2026-06-05'}, format='json')
        self.assertEqual((cl1.status_code, cl2.status_code), (200, 200))
        self.assertEqual(cl2.data['closing_date'], '2026-06-01')

        # WhatsApp link & shipping PDFs
        wa = client.get(reverse('sale:sales-order-whatsapp', args=[so.pk]))
        self.assertIn('https://wa.me/8801711111111', wa.data['whatsapp_url'])
        self.assertTrue(client.get(reverse('report:shipping-pdf', args=['challan', ch1.data['id']])).content.startswith(b'%PDF'))


class M3ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='m3-race-admin')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        self.wh = WareHouse.objects.create(name='M3 Race WH', address='Dhaka', phone='01')
        self.cash = FinancialAccount.objects.create(
            name='M3 Race Cash', type=FinancialAccount.Type.CASH, warehouse=self.wh, opening_balance=1000,
        )
        self.pcs = UnitOfMeasure.objects.create(name='Piece', code='M3-RACE-PCS')
        self.prod = Product.objects.create(name='Race Last', sku='M3-RACE', unit=self.pcs, currency_id='BDT', sale_price=100)
        post_opening_stock(self.admin, [{'warehouse': self.wh.pk, 'product': self.prod.pk, 'quantity': '5'}], uuid4())
        self.coupon = Coupon.objects.create(code='ONCEONLY', coupon_type=Coupon.Type.FIXED, amount=Decimal('20'), quantity=1)
        self.reg, _ = open_register(self.admin, self.wh.pk, Decimal('0'), uuid4())

    def test_f_prc_3_coupon_race_one_redemption_wins(self):
        barrier = Barrier(2)
        results = []

        def worker():
            close_old_connections()
            actor = get_user_model().objects.get(pk=self.admin.pk)
            barrier.wait(timeout=10)
            for _ in range(30):
                try:
                    res, _ = pos_checkout(
                        actor,
                        {
                            'register': self.reg.pk, 'warehouse': self.wh.pk,
                            'coupon_code': 'ONCEONLY',
                            'lines': [{'product': self.prod.pk, 'quantity': '1', 'unit': self.pcs.pk}],
                            'tenders': [{'method': 'cash', 'account': self.cash.pk, 'amount': '80'}],
                        },
                        uuid4(),
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

        self.assertEqual(len([r for r in results if r[0] == 'ok']), 1, results)
        self.assertEqual(len([r for r in results if r[0] == 'err']), 1, results)
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.used_count, 1)
        self.assertEqual(CouponRedemption.objects.filter(coupon=self.coupon).count(), 1)
