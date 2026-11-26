"""Stored value and coupon operations shared by management and POS checkout."""
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounting.models import GiftCardLiabilityEntry
from common.codes import FORMATS, generate_code
from .models import Coupon, CouponRedemption, GiftCard, GiftCardTransaction


CENT = Decimal('0.01')
MAX_VALUE = Decimal('9999999999.99')
Kind = GiftCardTransaction.Kind


def insert_coded(model, purpose, supplied, **fields):
    column = FORMATS[purpose].field
    for _ in range(5):
        code = supplied or generate_code(purpose)
        try:
            with transaction.atomic():
                return model.objects.create(**{column: code}, **fields)
        except IntegrityError:
            if not model.objects.filter(**{column: code}).exists():
                raise
            if supplied:
                raise ValidationError({column: 'This code is already in use.'})
    raise ValidationError('Code generation was busy. Try again.')


def coupon_discount(coupon, subtotal):
    if not coupon.is_active or coupon.used >= coupon.quantity:
        raise ValidationError('Coupon is inactive or exhausted.')
    if subtotal <= 0:
        raise ValidationError('A coupon requires a positive eligible subtotal.')
    discount = coupon.value if coupon.kind == Coupon.Kind.FLAT else subtotal * coupon.value / 100
    return min(subtotal, discount.quantize(CENT, rounding=ROUND_HALF_UP))


@transaction.atomic
def consume_coupon(code, sale, subtotal):
    """Call with the server-priced subtotal inside the sale completion transaction."""
    coupons = Coupon.objects.filter(code=code.upper())
    coupons.update(used=F('used'))  # Serialize the read on SQLite as well.
    coupon = coupons.select_for_update().first()
    if not coupon or sale.currency_id != settings.INVENTORY_BASE_CURRENCY:
        raise ValidationError('Invalid coupon or sale currency.')
    previous = CouponRedemption.objects.filter(sale=sale).first()
    if previous:
        if previous.coupon_id != coupon.pk:
            raise ValidationError('This sale already used a different coupon.')
        return previous
    discount = coupon_discount(coupon, subtotal)
    if not discount:
        raise ValidationError('The rounded discount is zero.')
    updated = coupons.filter(is_active=True, used__lt=F('quantity')).update(used=F('used') + 1)
    if not updated:
        raise ValidationError('Coupon has no remaining uses.')
    return CouponRedemption.objects.create(coupon=coupon, sale=sale, discount=discount)


def record_entry(card, kind, amount, actor, key, sale=None, sale_return=None):
    entry = card.entries.create(kind=kind, amount=amount, balance_after=card.balance, created_by=actor,
                                request_key=key, sale=sale, sale_return=sale_return)
    GiftCardLiabilityEntry.objects.create(transaction=entry, movement=-amount if kind == Kind.REDEEM else amount)
    return entry


@transaction.atomic
def issue_card(data, actor):
    fields = data.copy()
    key = fields.pop('request_key', None) or uuid4()
    previous = GiftCardTransaction.objects.filter(request_key=key).select_related('card').first()
    if previous:
        card = previous.card
        if (previous.kind == Kind.ISSUE and previous.created_by_id == actor.pk and previous.amount == fields['amount']
                and card.is_deposit == fields.get('is_deposit', False)
                and card.customer == fields.get('customer') and card.user == fields.get('user')
                and card.expired_date == fields['expired_date'] and fields.get('card_no') in ('', None, card.card_no)):
            return card
        raise ValidationError('This request key was already used.')
    card = insert_coded(GiftCard, 'gift_card', fields.pop('card_no', None), created_by=actor, **fields)
    record_entry(card, Kind.ISSUE, card.amount, actor, key)
    return card


def validate_card(card, customer_id=None, owner_user_id=None):
    if not card.is_active or card.expired_date < timezone.localdate():
        raise ValidationError('Gift card is inactive or expired.')
    if getattr(settings, 'POS_STRICT_GIFT_CARD_OWNER', True):
        if card.customer_id and card.customer_id != customer_id or card.user_id and card.user_id != owner_user_id:
            raise ValidationError('Gift card belongs to a different owner.')
    if card.balance <= 0:
        raise ValidationError('Insufficient gift card balance.')


@transaction.atomic
def move_card(card_id, kind, amount, actor, key, *, sale=None, sale_return=None, owner_user_id=None):
    """Checkout passes its tender amount and verified purchaser identity in its outer transaction."""
    if amount <= 0 or amount > MAX_VALUE or amount != amount.quantize(CENT):
        raise ValidationError('Enter a positive amount with at most two decimal places.')
    cards = GiftCard.objects.filter(pk=card_id)
    cards.update(expense=F('expense'))  # First write also locks the balance on SQLite.
    card = cards.select_for_update().get()
    old = GiftCardTransaction.objects.filter(request_key=key).first()
    if old:
        if (old.card_id, old.kind, old.amount, old.created_by, old.sale, old.sale_return) != (
                card_id, kind, amount, actor, sale, sale_return):
            raise ValidationError('This request key was already used.')
        return old
    if sale and sale.currency_id != settings.INVENTORY_BASE_CURRENCY:
        raise ValidationError('Gift card tender requires a base-currency sale.')
    if kind == Kind.RECHARGE:
        if not card.is_active or card.expired_date < timezone.localdate() or card.amount + amount > MAX_VALUE:
            raise ValidationError('Card cannot accept this recharge.')
        cards.update(amount=F('amount') + amount)
    elif kind == Kind.REDEEM and sale:
        validate_card(card, sale.customer_id, owner_user_id)
        if card.entries.filter(kind=kind, sale=sale).exists():
            raise ValidationError('This card was already applied to the sale.')
        if not cards.filter(amount__gte=card.expense + amount, expense=card.expense).update(expense=card.expense + amount):
            raise ValidationError('Insufficient gift card balance.')
    elif kind == Kind.REFUND and sale and sale_return and sale_return.sales_order_id == sale.pk:
        redeemed = card.entries.filter(sale=sale, kind=Kind.REDEEM).aggregate(total=Sum('amount'))['total'] or 0
        refunded = card.entries.filter(sale=sale, kind=Kind.REFUND).aggregate(total=Sum('amount'))['total'] or 0
        if amount > redeemed - refunded or card.entries.filter(kind=kind, sale_return=sale_return).exists():
            raise ValidationError('Refund exceeds the remaining gift card tender or was already posted.')
        cards.update(expense=F('expense') - amount)
    else:
        raise ValidationError('Invalid gift card transaction.')
    card.refresh_from_db()
    return record_entry(card, kind, amount, actor, key, sale, sale_return)

