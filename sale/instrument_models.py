from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Coupon(models.Model):
    class Kind(models.TextChoices):
        FLAT = 'flat', 'Flat amount'
        PERCENT = 'percentage', 'Percentage'

    code = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=10, choices=Kind)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField()
    used = models.PositiveIntegerField(default=0, editable=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0, used__lte=F('quantity')), name='coupon_usage_limit'),
            models.CheckConstraint(condition=Q(value__gt=0) & (Q(kind='flat') | Q(kind='percentage', value__lte=100)),
                                   name='coupon_discount_range'),
        ]


class CouponRedemption(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT)
    sale = models.OneToOneField('sale.SalesOrder', on_delete=models.PROTECT)
    discount = models.DecimalField(max_digits=18, decimal_places=4)
    created_at = models.DateTimeField(auto_now_add=True)


class GiftCard(models.Model):
    is_deposit = models.BooleanField(default=False)
    card_no = models.CharField(max_length=16, unique=True)
    customer = models.ForeignKey('people.Party', null=True, blank=True, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    expense = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expired_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='issued_gift_cards')
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def balance(self):
        return self.amount - self.expense

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0, expense__gte=0, expense__lte=F('amount')),
                                   name='gift_card_balance_valid'),
            models.CheckConstraint(condition=Q(customer__isnull=True) | Q(user__isnull=True), name='gift_card_one_owner'),
            models.UniqueConstraint(fields=['customer'], condition=Q(is_deposit=True), name='pos_one_customer_deposit'),
            models.CheckConstraint(condition=Q(is_deposit=False) | Q(customer__isnull=False), name='pos_deposit_customer'),
        ]


class GiftCardTransaction(models.Model):
    class Kind(models.TextChoices):
        ISSUE = 'issue', 'Issued'
        RECHARGE = 'recharge', 'Recharged'
        REDEEM = 'redeem', 'Redeemed'
        REFUND = 'refund', 'Refunded'

    card = models.ForeignKey(GiftCard, related_name='entries', on_delete=models.PROTECT)
    kind = models.CharField(max_length=10, choices=Kind)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    request_key = models.UUIDField(unique=True)
    sale = models.ForeignKey('sale.SalesOrder', null=True, on_delete=models.PROTECT)
    sale_return = models.ForeignKey('sale.SalesReturn', null=True, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0, balance_after__gte=0), name='gift_entry_amount_valid'),
            models.UniqueConstraint(fields=['card', 'sale'], condition=Q(kind='redeem'), name='gift_once_per_sale'),
            models.UniqueConstraint(fields=['card', 'sale_return'], condition=Q(kind='refund'), name='gift_once_per_return'),
        ]

