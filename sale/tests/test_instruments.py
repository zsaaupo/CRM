from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from time import sleep
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounting.models import GiftCardLiabilityEntry
from common.models import TradeStatus
from inventory.models import Currency
from sale.instruments import Kind, consume_coupon, issue_card, move_card
from sale.models import Coupon, GiftCard, SalesOrder, SalesReturn


class InstrumentTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(username='issuer')
        self.client = APIClient()
        self.client.force_login(self.actor)
        self.expiry = (timezone.localdate() + timedelta(days=7)).isoformat()

    def post(self, name, data, *args):
        return self.client.post(reverse(name, args=args), data, format='json')

    def card(self, **overrides):
        data = dict(card_no='0012345678901234', amount='100.00', expired_date=self.expiry)
        data.update(overrides)
        response = self.post('sale:gift-cards', data)
        self.assertEqual(response.status_code, 201, response.content)
        return GiftCard.objects.get(pk=response.data['id'])

    def test_coupon_global_quantity_retry_cap_and_rollback(self):
        data = dict(code='event', kind='percentage', value='25', quantity=1)
        self.assertEqual(self.post('sale:coupons', data).status_code, 201)
        self.assertEqual(self.post('sale:coupons', data).status_code, 400)
        order = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1)
        second = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1)
        with self.assertRaises(RuntimeError), transaction.atomic():
            consume_coupon('event', order, Decimal('100'))
            raise RuntimeError
        first = consume_coupon('event', order, Decimal('100'))
        self.assertEqual(first.discount, Decimal('25'))
        self.assertEqual(consume_coupon('EVENT', order, Decimal('100')).pk, first.pk)
        with self.assertRaises(ValidationError):
            consume_coupon('event', second, Decimal('100'))
        self.assertEqual(Coupon.objects.get().used, 1)
        self.assertEqual(self.post('sale:coupons', dict(kind='flat', value='200', quantity=2)).status_code, 201)
        generated = Coupon.objects.exclude(code='EVENT').get().code
        check = self.post('sale:coupon-validate', {'code': generated, 'subtotal': '10'})
        self.assertEqual(check.data['discount'], '10.00')
        self.assertEqual(self.post('sale:coupons', {**data, 'value': 101}).status_code, 400)

    def test_load_spend_refund_and_liability(self):
        card = self.card()
        order = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1)
        returned = SalesReturn.objects.create(sales_order=order)
        key = uuid4()
        for _ in range(2):
            response = self.post('sale:gift-card-recharge', dict(amount=20, request_key=key), card.pk)
            self.assertEqual(response.status_code, 200, response.content)
        move_card(card.pk, Kind.REDEEM, Decimal('75'), self.actor, uuid4(), sale=order)
        with self.assertRaises(ValidationError):
            second = SalesOrder.objects.create(currency_id='BDT', exchange_rate=1)
            move_card(card.pk, Kind.REDEEM, Decimal('46'), self.actor, uuid4(), sale=second)
        with self.assertRaises(ValidationError):
            move_card(card.pk, Kind.REFUND, Decimal('76'), self.actor, uuid4(), sale=order, sale_return=returned)
        move_card(card.pk, Kind.REFUND, Decimal('30'), self.actor, uuid4(), sale=order, sale_return=returned)
        card.refresh_from_db()
        self.assertEqual(card.balance, Decimal('75'))
        self.assertEqual(card.entries.count(), 4)
        self.assertEqual(sum(GiftCardLiabilityEntry.objects.values_list('movement', flat=True)), card.balance)
        self.assertEqual(self.post('sale:gift-card-deactivate', {'ids': [card.pk]}).status_code, 400)

    def test_issuance_retry_and_owner_expiry(self):
        key = uuid4()
        card = self.card(request_key=key, user_id=self.actor.pk)
        self.assertEqual(self.card(request_key=key, user_id=self.actor.pk).pk, card.pk)
        validate = lambda: self.post('sale:gift-card-validate', {'card_no': card.card_no})
        self.assertEqual(validate().status_code, 200)
        preview = self.post('sale:gift-card-validate', {'card_no': card.card_no, 'payable_amount': '150.00'})
        self.assertEqual((preview.data['tender_amount'], preview.data['remaining_due']), ('100.00', '50.00'))
        GiftCard.objects.update(expired_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(validate().status_code, 400)
        self.assertEqual(self.post('sale:gift-card-expiry', dict(expired_date=self.expiry), card.pk).status_code, 200)
        other = get_user_model().objects.create_superuser(username='other')
        self.client.force_login(other)
        self.assertEqual(validate().status_code, 400)

    def test_download_list_generation_and_permissions(self):
        card = self.card()
        image_url = reverse('sale_pages:gift-card-image', args=[card.pk]) + '?download=1'
        response = self.client.get(image_url)
        self.assertTrue(response.content.startswith(b'\x89PNG'))
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertContains(self.client.get(reverse('sale_pages:gift-card-detail', args=[card.pk])), 'Download card')
        self.assertContains(self.client.get(reverse('sale_pages:coupons')), 'Total uses across all tills')
        self.assertContains(self.client.get(reverse('sale_pages:gift-cards')), 'Issue gift card')
        url = reverse('sale:gift-cards')
        self.assertEqual(self.client.get(url).data['totals']['balance'], '100.00')
        self.assertIn(card.card_no.encode(), self.client.get(url, {'export': 'csv'}).content)
        self.assertTrue(self.client.get(url, {'export': 'xlsx'}).content.startswith(b'PK'))
        self.assertRegex(self.post('common:generate-code', dict(purpose='gift_card')).data['code'], r'^[0-9]{16}$')
        self.client.force_login(get_user_model().objects.create_user(username='viewer'))
        self.assertEqual(self.client.get(image_url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.post('common:generate-code', dict(purpose='coupon')).status_code, 403)


class TillConcurrencyTests(TransactionTestCase):
    def test_two_tills_compete_for_last_use_and_last_balance(self):
        actor = get_user_model().objects.create_user(username='till')
        Currency.objects.get_or_create(code='BDT', defaults={'name': 'Taka', 'exchange_rate': 1})
        TradeStatus.objects.get_or_create(code='draft', defaults={'name': 'Draft'})
        orders = [SalesOrder.objects.create(currency_id='BDT', exchange_rate=1) for _ in range(2)]
        coupon = Coupon.objects.create(code='LAST', kind='flat', value=1, quantity=1, created_by=actor)
        card = issue_card(dict(amount=Decimal('1.00'), expired_date=timezone.localdate()), actor)
        barrier = Barrier(2)

        def pay(order):
            close_old_connections()
            key = uuid4()
            try:
                barrier.wait(timeout=10)
                for attempt in range(20):
                    try:
                        with transaction.atomic():
                            move_card(card.pk, Kind.REDEEM, Decimal('1.00'), actor, key, sale=order)
                            consume_coupon('LAST', order, Decimal('2.00'))
                        return True
                    except ValidationError:
                        return False
                    except OperationalError as exc:
                        if 'locked' not in str(exc).lower() or attempt == 19:
                            raise
                        sleep(.02)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(pay, orders)), [False, True])
        coupon.refresh_from_db()
        card.refresh_from_db()
        self.assertEqual((coupon.used, card.balance, card.entries.count()), (1, 0, 2))

