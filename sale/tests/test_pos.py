from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import sleep
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import FinancialAccount, SalesPayment
from accounting.services import sales_payment_totals
from common.models import ProductStatus, TradeStatus
from inventory.models import Currency, Product, ProductType, UnitOfMeasure
from people.models import Party
from sale.instruments import issue_card
from sale.models import Coupon, GiftCard, POSTicket, SalesOrder, SalesReturn, SalesReturnItem
from sale.returns import complete_return
from warehouse.models import Stock, WareHouse


class POSCheckoutTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(username='cashier')
        self.client = APIClient()
        self.client.force_login(self.actor)
        self.w = WareHouse.objects.create(name='Main', address='Main', phone='1')
        self.unit = UnitOfMeasure.objects.create(code='PC', name='Piece')
        self.product = Product.objects.create(name='Scanner', sku='POS1', barcode='00101', currency_id='BDT',
                                              unit=self.unit, status_id='active', sale_price=10, product_tax=10,
                                              is_featured=True)
        self.stock = Stock.objects.create(warehouse=self.w, product=self.product, quantity=2)
        self.account = FinancialAccount.objects.create(type='cash', warehouse=self.w, name='Till')
        self.cart = dict(warehouse=self.w.pk, items=[dict(product=self.product.pk, quantity='2')])
        self.key = str(uuid4())

    def post(self, name, data):
        return self.client.post(reverse('sale:pos-' + name), data, format='json')

    def payload(self, tenders, revision=0):
        result = self.post('quote', self.cart)
        self.assertEqual(result.status_code, 200, result.data)
        return dict(key=self.key, cart=self.cart, revision=revision, quote=result.data['quote'], tenders=tenders)

    def test_catalog_and_page(self):
        self.assertContains(self.client.get(reverse('sale_pages:pos')), 'Take payment')
        for params in ({'scan': '00101'}, {'scan': 'pos1'}, {'featured': '1'}):
            response = self.client.get(reverse('sale:pos-catalog'), {'warehouse': self.w.pk, **params})
            self.assertEqual([p['id'] for p in response.data['results']], [self.product.pk])

    def test_fractional_sale_promotion_discounts_card_and_cheque(self):
        Product.objects.filter(pk=self.product.pk).update(promo_price=8)
        bank = FinancialAccount.objects.create(name='Bank', type='bank', warehouse=self.w)
        self.cart.update(items=[dict(product=self.product.pk, quantity='1.5', unit_discount='1')],
                         discount_type='percentage', discount=10, tax_rate=5, shipping='0.50')
        payload = self.payload([dict(method='card', amount='5.00', reference='terminal-01', account=bank.pk),
                                dict(method='cheque', amount='5.42', reference='cheque-01', account=bank.pk)])
        response = self.post('checkout', payload)
        self.assertEqual(response.status_code, 200, response.data)
        receipt = response.data['receipt']
        self.assertEqual((receipt['subtotal'], receipt['discount'], receipt['tax'], receipt['total']),
                         ('10.50', '1.05', '0.47', '10.42'))
        self.assertEqual([p['method'] for p in receipt['tenders']], ['card', 'cheque'])
        self.assertEqual(bank.transactions.first().balance_after, Decimal('10.42'))
        self.assertEqual(self.client.get(reverse('sale:pos-history'), {'warehouse': self.w.pk}).data['results'][0]['key'], self.key)

    def test_park_resume_split_coupon_cash_change_return(self):
        coupon = Coupon.objects.create(code='POS', kind='flat', value=2, quantity=1, created_by=self.actor)
        card = issue_card(dict(amount=Decimal('9'), expired_date=timezone.localdate()), self.actor)
        self.cart.update(coupon='pos', shipping='1')
        draft = self.post('draft', dict(key=self.key, revision=0, cart=self.cart))
        self.assertEqual(draft.status_code, 200, draft.data)
        self.assertFalse(SalesOrder.objects.exists())
        payload = self.payload([dict(method='gift_card', card_no=card.card_no, amount='9'),
                                dict(method='cash', account=self.account.pk, amount='11.80', received='20')], 1)
        result = self.post('checkout', payload)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['receipt']['tenders'][1]['change'], '8.20')
        self.assertEqual(self.post('checkout', payload).data['order'], result.data['order'])
        coupon.refresh_from_db(); card.refresh_from_db(); self.stock.refresh_from_db()
        self.assertEqual((coupon.used, card.balance, self.stock.quantity), (1, 0, 0))
        order = SalesOrder.objects.get()
        self.assertEqual(sales_payment_totals(order), (Decimal('20.80'), Decimal('20.80'), 0))
        self.assertEqual(SalesPayment.objects.get().amount, Decimal('11.80'))
        returned = SalesReturn.objects.create(sales_order=order)
        SalesReturnItem.objects.create(sales_return=returned, sales_order_item=order.items.get(), quantity=1)
        complete_return(self.actor, returned.pk)
        card.refresh_from_db()
        self.assertEqual(card.balance, 9)
        self.assertEqual(returned.refund_payments.get().amount, Decimal('.90'))
        self.assertEqual(sales_payment_totals(order)[2], Decimal('9.90'))

    def test_stock_failure_and_invalid_second_tender_roll_back(self):
        card = issue_card(dict(amount=Decimal('4'), expired_date=timezone.localdate()), self.actor)
        payload = self.payload([dict(method='gift_card', card_no=card.card_no, amount='4'),
                                dict(method='card', account=self.account.pk, reference='test-ref', amount='18')])
        self.assertEqual(self.post('checkout', payload).status_code, 400)
        self.stock.refresh_from_db(); card.refresh_from_db()
        self.assertEqual((self.stock.quantity, card.balance), (2, 4))
        self.assertFalse(POSTicket.objects.exists())
        Stock.objects.update(quantity=0)
        payload['tenders'] = [dict(method='cash', account=self.account.pk, amount='22')]
        self.assertEqual(self.post('checkout', payload).status_code, 400)
        self.assertFalse(SalesOrder.objects.exists())

    def test_deposit_ownership_and_stale_price(self):
        owner = Party.objects.create(name='Customer', code='DP', is_customer=True)
        self.cart['customer'] = owner.pk
        response = self.client.post(reverse('sale:gift-cards'), dict(customer_id=owner.pk, amount='22',
                                    is_deposit=True), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        tender = [dict(method='deposit', amount='22')]
        payload = self.payload(tender)
        Product.objects.update(sale_price=11)
        self.assertEqual(self.post('checkout', payload).status_code, 409)
        Product.objects.update(sale_price=10)
        self.cart['customer'] = None
        self.assertEqual(self.post('checkout', self.payload(tender)).status_code, 400)
        self.cart['customer'] = owner.pk
        self.assertEqual(self.post('checkout', self.payload(tender)).status_code, 200)
        self.assertEqual(GiftCard.objects.get().balance, 0)
        self.assertFalse(SalesPayment.objects.exists())

    def test_cancel_draft_conflict_and_scope(self):
        draft = dict(key=self.key, revision=0, cart=self.cart)
        self.assertEqual(self.post('draft', draft).status_code, 200)
        url = reverse('sale:pos-ticket', args=[self.key])
        self.assertEqual(self.client.post(url, {'revision': 1}, format='json').status_code, 200)
        self.assertEqual(self.post('checkout', self.payload([], 1)).status_code, 409)
        self.client.force_login(get_user_model().objects.create_user(username='unassigned'))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.post('quote', self.cart).status_code, 403)


class POSRetryTests(TransactionTestCase):
    def test_concurrent_identical_checkout_posts_once(self):
        actor = get_user_model().objects.create_superuser(username='retry-cashier')
        warehouse = WareHouse.objects.create(name='Till', address='Main', phone='1')
        account = FinancialAccount.objects.create(type='cash', name='Till cash', warehouse=warehouse)
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        ProductType.objects.get_or_create(code='goods', defaults={'name': 'Goods'})
        ProductStatus.objects.get_or_create(code='active', defaults={'name': 'Active'})
        unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')
        product = Product.objects.create(name='Item', sku='LAST', unit=unit, currency_id='BDT', status_id='active', sale_price=5)
        stock = Stock.objects.create(product=product, warehouse=warehouse, quantity=1)
        cart = dict(warehouse=warehouse.pk, items=[dict(product=product.pk, quantity=1)])
        client = APIClient()
        client.force_authenticate(actor)
        quote = client.post(reverse('sale:pos-quote'), cart, format='json').data['quote']
        payload = dict(key=str(uuid4()), cart=cart, quote=quote, tenders=[dict(method='cash', account=account.pk, amount=5)])
        start = Barrier(2)

        def submit(_):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=actor.pk))
                start.wait(timeout=10)
                for _ in range(30):
                    response = client.post(reverse('sale:pos-checkout'), payload, format='json')
                    if response.status_code != 409:
                        self.assertEqual(response.status_code, 200, response.data)
                        return response.data['order']
                    sleep(.03)
                self.fail('The concurrent checkout never resolved.')
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            orders = list(pool.map(submit, range(2)))
        stock.refresh_from_db()
        self.assertEqual(orders, [SalesOrder.objects.get().pk] * 2)
        self.assertEqual((stock.quantity, SalesPayment.objects.get().amount, POSTicket.objects.count()), (0, 5, 1))
