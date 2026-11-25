"""Gift cards, customer deposits, coupons, and reward points (PRICE-01)."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounting.models import AccountTransaction, DepositEntry, FinancialAccount
from accounting.services import lock_rows, post_transaction
from common.exceptions import StateConflict
from common.idempotency import IdempotentResult, run_idempotent
from common.money import q_money
from common.settings_store import get_general
from people.models import CustomerPointLedger, Party
from .models import Coupon, CouponRedemption, GiftCard, GiftCardEntry


ZERO = Decimal('0')


def _recompute_gift_card_balance(card: GiftCard) -> Decimal:
    total = card.entries.aggregate(s=Sum('amount'))['s'] or ZERO
    bal = q_money(total, digits=4)
    GiftCard.objects.filter(pk=card.pk).update(balance=bal)
    card.balance = bal
    return bal


def recompute_deposit_balance(customer_id: int) -> Decimal:
    credits = DepositEntry.objects.filter(customer_id=customer_id, kind=DepositEntry.Kind.CREDIT).aggregate(s=Sum('amount'))['s'] or ZERO
    debits = DepositEntry.objects.filter(customer_id=customer_id, kind=DepositEntry.Kind.DEBIT).aggregate(s=Sum('amount'))['s'] or ZERO
    bal = q_money(credits - debits, digits=4)
    Party.all_objects.filter(pk=customer_id).update(deposit_balance=bal)
    return bal


def issue_gift_card(actor, card_no: str, amount, *, customer=None, expired_date=None, account_id=None, request_key=None):
    amt = q_money(amount, digits=4)
    if amt <= ZERO:
        raise ValidationError({'amount': 'Gift card amount must be positive.'})
    clean_no = (card_no or '').strip()
    if not clean_no:
        raise ValidationError({'card_no': 'Gift card number is required.'})
    key = request_key or uuid4()

    def _do():
        with transaction.atomic():
            if GiftCard.objects.filter(card_no=clean_no).exists():
                raise ValidationError({'card_no': f'Gift card "{clean_no}" already exists.'})
            username = actor.get_username() if actor else 'system'
            cust_id = customer.pk if hasattr(customer, 'pk') else customer
            card = GiftCard.objects.create(
                card_no=clean_no, amount=amt, balance=amt, customer_id=cust_id,
                expired_date=expired_date, create_by=username, modified_by=username,
            )
            GiftCardEntry.objects.create(
                gift_card=card, kind=GiftCardEntry.Kind.ISSUE, amount=amt,
                note='Issued gift card', request_key=key, create_by=username, modified_by=username,
            )
            if account_id:
                acct = lock_rows(FinancialAccount.objects.filter(pk=account_id, is_active=True)).first()
                if acct is None:
                    raise ValidationError({'account': 'Choose an active financial account.'})
                post_transaction(
                    acct, AccountTransaction.Direction.INFLOW, actor,
                    date=timezone.localdate(), amount=amt,
                    source_kind=AccountTransaction.SourceKind.GIFT_CARD_SALE,
                    source_ref_id=card.pk, source_reference=card.card_no,
                )
            return IdempotentResult('gift_card', card.pk, {'id': card.pk, 'card_no': card.card_no, 'balance': str(card.balance)}, value=card)

    return run_idempotent(key, f'gift_card_issue:{clean_no}', {'card_no': clean_no, 'amount': str(amt)}, _do,
                          loader=lambda r: GiftCard.objects.get(pk=r.result_id))


def recharge_gift_card(actor, card_id: int, amount, *, account_id=None, request_key=None):
    amt = q_money(amount, digits=4)
    if amt <= ZERO:
        raise ValidationError({'amount': 'Recharge amount must be positive.'})
    key = request_key or uuid4()

    def _do():
        with transaction.atomic():
            GiftCard.objects.filter(pk=card_id).update(updated_at=F('updated_at'))
            card = GiftCard.objects.select_for_update().get(pk=card_id)
            if not card.is_active:
                raise ValidationError({'gift_card': 'This gift card is inactive.'})
            username = actor.get_username() if actor else 'system'
            GiftCardEntry.objects.create(
                gift_card=card, kind=GiftCardEntry.Kind.RECHARGE, amount=amt,
                note='Gift card recharge', request_key=key, create_by=username, modified_by=username,
            )
            _recompute_gift_card_balance(card)
            if account_id:
                acct = lock_rows(FinancialAccount.objects.filter(pk=account_id, is_active=True)).first()
                if acct is None:
                    raise ValidationError({'account': 'Choose an active financial account.'})
                post_transaction(
                    acct, AccountTransaction.Direction.INFLOW, actor,
                    date=timezone.localdate(), amount=amt,
                    source_kind=AccountTransaction.SourceKind.GIFT_CARD_SALE,
                    source_ref_id=card.pk, source_reference=f'{card.card_no} recharge',
                )
            return IdempotentResult('gift_card_recharge', card.pk, {'id': card.pk, 'balance': str(card.balance)}, value=card)

    return run_idempotent(key, f'gift_card_recharge:{card_id}', {'card_id': card_id, 'amount': str(amt)}, _do,
                          loader=lambda r: GiftCard.objects.get(pk=r.result_id))


def redeem_gift_card(gift_card_id: int, amount: Decimal, sales_payment_id: int, request_key, *, actor=None):
    if GiftCardEntry.objects.filter(request_key=request_key).exists():
        return GiftCard.objects.get(pk=gift_card_id)
    amt = q_money(amount, digits=4)
    if amt <= ZERO:
        raise ValidationError({'gift_card': 'Gift card redemption amount must be positive.'})
    GiftCard.objects.filter(pk=gift_card_id).update(updated_at=F('updated_at'))
    card = GiftCard.objects.select_for_update().filter(pk=gift_card_id).first()
    if card is None or not card.is_active:
        raise ValidationError({'gift_card': 'Choose an active gift card.'})
    if card.expired_date and card.expired_date < timezone.localdate():
        raise ValidationError({'gift_card': f'Gift card {card.card_no} expired on {card.expired_date}.'})
    bal = _recompute_gift_card_balance(card)
    if amt > bal:
        raise ValidationError({'gift_card': f'Gift card {card.card_no} balance ({bal}) is less than {amt}.'})
    username = actor.get_username() if actor else 'system'
    GiftCardEntry.objects.create(
        gift_card=card, kind=GiftCardEntry.Kind.REDEEM, amount=-amt,
        sales_payment_id=sales_payment_id, request_key=request_key,
        create_by=username, modified_by=username,
    )
    _recompute_gift_card_balance(card)
    return card


def reverse_gift_card_redemption(gift_card_id: int, amount: Decimal, sales_payment_id: int, request_key, *, actor=None):
    if GiftCardEntry.objects.filter(request_key=request_key).exists():
        return GiftCard.objects.get(pk=gift_card_id)
    amt = q_money(amount, digits=4)
    GiftCard.objects.filter(pk=gift_card_id).update(updated_at=F('updated_at'))
    card = GiftCard.objects.select_for_update().get(pk=gift_card_id)
    username = actor.get_username() if actor else 'system'
    GiftCardEntry.objects.create(
        gift_card=card, kind=GiftCardEntry.Kind.REVERSAL, amount=amt,
        sales_payment_id=sales_payment_id, request_key=request_key,
        create_by=username, modified_by=username,
    )
    _recompute_gift_card_balance(card)
    return card


def add_deposit(actor, customer_id: int, amount, account_id: int, note: str, request_key):
    amt = q_money(amount, digits=4)
    if amt <= ZERO:
        raise ValidationError({'amount': 'Deposit amount must be positive.'})
    key = request_key or uuid4()

    def _do():
        with transaction.atomic():
            Party.all_objects.filter(pk=customer_id).update(updated_at=F('updated_at'))
            cust = Party.objects.select_for_update().filter(pk=customer_id, is_customer=True, is_active=True).first()
            if cust is None:
                raise ValidationError({'customer': 'Choose an active customer.'})
            acct = lock_rows(FinancialAccount.objects.filter(pk=account_id, is_active=True)).first()
            if acct is None:
                raise ValidationError({'account': 'Choose an active financial account.'})
            username = actor.get_username() if actor else 'system'
            entry = DepositEntry.objects.create(
                customer=cust, kind=DepositEntry.Kind.CREDIT, amount=amt,
                account=acct, source=DepositEntry.Source.DEPOSIT, note=note or '',
                request_key=key, create_by=username, modified_by=username,
            )
            post_transaction(
                acct, AccountTransaction.Direction.INFLOW, actor,
                date=timezone.localdate(), amount=amt,
                source_kind=AccountTransaction.SourceKind.CUSTOMER_DEPOSIT,
                source_ref_id=entry.pk, source_reference=cust.code,
            )
            new_bal = recompute_deposit_balance(cust.pk)
            return IdempotentResult('deposit_entry', entry.pk, {'id': entry.pk, 'balance': str(new_bal)}, value=entry)

    return run_idempotent(key, f'customer_deposit:{customer_id}', {'customer_id': customer_id, 'amount': str(amt)}, _do,
                          loader=lambda r: DepositEntry.objects.get(pk=r.result_id))


def add_deposit_from_excess(actor, customer_id: int, amount: Decimal, account_id: int | None, note: str, request_key):
    if DepositEntry.objects.filter(request_key=request_key).exists():
        return
    amt = q_money(amount, digits=4)
    if amt <= ZERO:
        return
    username = actor.get_username() if actor else 'system'
    DepositEntry.objects.create(
        customer_id=customer_id, kind=DepositEntry.Kind.CREDIT, amount=amt,
        account_id=account_id, source=DepositEntry.Source.PAYMENT, note=note or 'Overpayment to deposit',
        request_key=request_key, create_by=username, modified_by=username,
    )
    recompute_deposit_balance(customer_id)


def debit_deposit(customer_id: int, amount: Decimal, sales_payment_id: int, request_key, *, actor=None):
    if DepositEntry.objects.filter(request_key=request_key).exists():
        return
    amt = q_money(amount, digits=4)
    if amt <= ZERO:
        raise ValidationError({'deposit': 'Deposit payment amount must be positive.'})
    Party.all_objects.filter(pk=customer_id).update(updated_at=F('updated_at'))
    cust = Party.all_objects.select_for_update().filter(pk=customer_id, is_customer=True).first()
    if cust is None:
        raise ValidationError({'customer': 'Customer not found for deposit.'})
    bal = recompute_deposit_balance(cust.pk)
    if amt > bal:
        raise ValidationError({'deposit': f'Customer deposit balance ({bal}) is less than {amt}.'})
    username = actor.get_username() if actor else 'system'
    DepositEntry.objects.create(
        customer=cust, kind=DepositEntry.Kind.DEBIT, amount=amt,
        source=DepositEntry.Source.PAYMENT, sales_payment_id=sales_payment_id,
        request_key=request_key, create_by=username, modified_by=username,
    )
    recompute_deposit_balance(cust.pk)


def credit_deposit_reversal(customer_id: int, amount: Decimal, sales_payment_id: int, request_key, *, actor=None):
    if DepositEntry.objects.filter(request_key=request_key).exists():
        return
    amt = q_money(amount, digits=4)
    username = actor.get_username() if actor else 'system'
    DepositEntry.objects.create(
        customer_id=customer_id, kind=DepositEntry.Kind.CREDIT, amount=amt,
        source=DepositEntry.Source.REFUND, sales_payment_id=sales_payment_id,
        request_key=request_key, create_by=username, modified_by=username,
    )
    recompute_deposit_balance(customer_id)


credit_deposit_refund = credit_deposit_reversal


def validate_coupon(code: str, order_subtotal: Decimal, *, doc_date=None) -> tuple[Coupon, Decimal]:
    clean = (code or '').strip().upper()
    if not clean:
        raise ValidationError({'coupon_code': 'Enter a coupon code.'})
    coupon = Coupon.objects.filter(code=clean).first()
    if coupon is None or not coupon.is_active:
        raise ValidationError({'coupon_code': f'Coupon "{clean}" is invalid or inactive.'})
    check_date = doc_date or timezone.localdate()
    if coupon.expired_date and coupon.expired_date < check_date:
        raise ValidationError({'coupon_code': f'Coupon "{clean}" expired on {coupon.expired_date}.'})
    if coupon.used_count >= coupon.quantity:
        raise ValidationError({'coupon_code': f'Coupon "{clean}" has reached its redemption limit.'})
    sub = Decimal(order_subtotal)
    if sub < Decimal(coupon.minimum_amount):
        raise ValidationError({'coupon_code': f'Minimum order amount {coupon.minimum_amount} required for "{clean}".'})
    if coupon.coupon_type == Coupon.Type.PERCENTAGE:
        disc = q_money(sub * Decimal(coupon.amount) / Decimal('100'), digits=4)
    else:
        disc = q_money(min(Decimal(coupon.amount), sub), digits=4)
    return coupon, min(disc, sub)


def redeem_coupon(code: str, order_subtotal: Decimal, sales_order=None, *, customer=None, doc_date=None, actor=None) -> Decimal:
    clean = (code or '').strip().upper()
    Coupon.objects.filter(code=clean).update(updated_at=F('updated_at'))
    coupon = Coupon.objects.select_for_update().filter(code=clean).first()
    if coupon is None:
        raise ValidationError({'coupon_code': f'Coupon "{clean}" not found.'})
    _, disc = validate_coupon(clean, order_subtotal, doc_date=doc_date)
    if sales_order is not None:
        username = actor.get_username() if actor else 'system'
        updated = Coupon.objects.filter(pk=coupon.pk, used_count__lt=F('quantity')).update(used_count=F('used_count') + 1)
        if not updated:
            raise StateConflict(f'Coupon "{clean}" has no remaining redemptions.')
        try:
            CouponRedemption.objects.create(
                coupon=coupon, sales_order=sales_order,
                customer=customer or sales_order.customer, amount=disc,
                create_by=username, modified_by=username,
            )
        except IntegrityError as exc:
            raise StateConflict(f'Coupon "{clean}" was already redeemed for this order.') from exc
    return disc


def _reward_config():
    try:
        from common.models import RewardSetting
        return RewardSetting.objects.first()
    except Exception:
        return None


def _available_reward_points(customer_id: int, *, as_of=None) -> int:
    today = as_of or timezone.localdate()
    qs = CustomerPointLedger.objects.filter(customer_id=customer_id).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gte=today),
    )
    total = qs.aggregate(s=Sum('points_delta'))['s'] or 0
    return max(0, int(total))


def _recompute_reward_points(customer_id: int, *, as_of=None) -> int:
    avail = _available_reward_points(customer_id, as_of=as_of)
    Party.all_objects.filter(pk=customer_id).update(reward_points=avail)
    return avail


def maybe_earn_points_for_order(order, *, actor=None) -> int:
    if not order.customer_id:
        return 0
    cfg = _reward_config()
    if cfg is not None and not getattr(cfg, 'enable_reward_points', False):
        return 0
    per_point = Decimal(getattr(cfg, 'sold_amount_per_point', Decimal('100')) or Decimal('100'))
    min_amt = Decimal(getattr(cfg, 'minimum_sold_amount_to_get_point', Decimal('0')) or ZERO)
    if cfg is None or per_point <= ZERO:
        return 0
    req_key = f'earn:sales_order:{order.pk}'
    if CustomerPointLedger.objects.filter(request_key=req_key).exists():
        return 0
    grand = Decimal(order.grand_total)
    if grand < min_amt:
        return 0
    pts = int(grand // per_point)
    if pts <= 0:
        return 0
    exp = None
    dur = getattr(cfg, 'point_expiry_duration_days', None)
    if dur is None:
        dur = getattr(cfg, 'point_expiry_duration', None)
    if dur:
        unit = getattr(cfg, 'point_expiry_unit', 'days')
        exp = order.order_date + timedelta(days=int(dur) * (30 if unit == 'months' else 1))
    username = actor.get_username() if actor else 'system'
    CustomerPointLedger.objects.create(
        customer_id=order.customer_id,
        sales_order=order,
        points_delta=pts,
        reason=CustomerPointLedger.Reason.EARN,
        expires_at=exp,
        rule_snapshot={'sold_amount_per_point': str(per_point), 'grand_total': str(grand)},
        request_key=req_key,
        create_by=username,
        modified_by=username,
    )
    return _recompute_reward_points(order.customer_id)


def redeem_points(
    customer_id: int,
    points: int,
    *,
    sales_order=None,
    actor=None,
    request_key=None,
) -> dict:
    pts = int(points or 0)
    if pts <= 0:
        raise ValidationError({'points': 'Redeem points must be positive.'})
    cfg = _reward_config()
    if cfg is None or not getattr(cfg, 'enable_reward_points', False):
        raise ValidationError({'points': 'Reward points program is not enabled.'})
    rk = str(request_key or uuid4())
    with transaction.atomic():
        existing = CustomerPointLedger.objects.filter(request_key=rk).first()
        if existing is not None:
            per_val = Decimal(existing.rule_snapshot.get('redeem_amount_per_point', cfg.redeem_amount_per_point))
            redeemed = abs(int(existing.points_delta))
            return {
                'points_redeemed': redeemed,
                'monetary_value': str(q_money(Decimal(redeemed) * per_val, digits=4)),
                'remaining_points': _available_reward_points(customer_id),
                'idempotent_replay': True,
            }
        Party.all_objects.filter(pk=customer_id).update(updated_at=F('updated_at'))
        cust = Party.all_objects.select_for_update().filter(pk=customer_id, is_customer=True).first()
        if cust is None:
            raise ValidationError({'customer': 'Customer not found for reward points.'})
        avail = _available_reward_points(cust.pk)
        if pts > avail:
            raise ValidationError({'points': f'Customer only has {avail} active reward points.'})
        per_val = Decimal(getattr(cfg, 'redeem_amount_per_point', Decimal('1.0000')) or Decimal('1.0000'))
        money_val = q_money(Decimal(pts) * per_val, digits=4)
        username = actor.get_username() if actor else 'system'
        CustomerPointLedger.objects.create(
            customer=cust,
            sales_order=sales_order,
            points_delta=-pts,
            reason=CustomerPointLedger.Reason.REDEEM,
            rule_snapshot={'redeem_amount_per_point': str(per_val), 'monetary_value': str(money_val)},
            request_key=rk,
            create_by=username,
            modified_by=username,
        )
        rem = _recompute_reward_points(cust.pk)
        return {
            'points_redeemed': pts,
            'monetary_value': str(money_val),
            'remaining_points': rem,
            'idempotent_replay': False,
        }


def expire_reward_points(*, as_of=None, actor=None) -> int:
    today = as_of or timezone.localdate()
    expired_customers = set(
        CustomerPointLedger.objects.filter(expires_at__lt=today, points_delta__gt=0).values_list('customer_id', flat=True)
    )
    updated = 0
    for cid in expired_customers:
        _recompute_reward_points(cid, as_of=today)
        updated += 1
    return updated


def reverse_points_for_return(sales_return, *, actor=None) -> int:
    order = sales_return.sales_order
    if not order.customer_id:
        return 0
    req_key = f'reverse:sales_return:{sales_return.pk}'
    if CustomerPointLedger.objects.filter(request_key=req_key).exists():
        return 0
    earned = CustomerPointLedger.objects.filter(sales_order=order, reason=CustomerPointLedger.Reason.EARN).first()
    if earned is None or earned.points_delta <= 0 or order.grand_total <= ZERO:
        return 0
    from accounting.services import return_refund_amount
    items = dict(sales_return.items.values_list('sales_order_item_id', 'quantity'))
    ret_val = return_refund_amount(order, items)
    ratio = min(Decimal('1'), Decimal(ret_val) / Decimal(order.grand_total * order.exchange_rate))
    already_rev = abs(
        CustomerPointLedger.objects.filter(
            sales_order=order, reason=CustomerPointLedger.Reason.REVERSE,
        ).aggregate(s=Sum('points_delta'))['s'] or 0
    )
    pts_rev = min(earned.points_delta - already_rev, int(Decimal(earned.points_delta) * ratio))
    if pts_rev <= 0:
        return 0
    username = actor.get_username() if actor else 'system'
    CustomerPointLedger.objects.create(
        customer_id=order.customer_id,
        sales_order=order,
        points_delta=-pts_rev,
        reason=CustomerPointLedger.Reason.REVERSE,
        rule_snapshot={'sales_return_id': sales_return.pk},
        request_key=req_key,
        create_by=username,
        modified_by=username,
    )
    return _recompute_reward_points(order.customer_id)
