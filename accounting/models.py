from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from common.models import TimeStampedModel


ZERO = Decimal('0.0000')
MIN_AMOUNT = Decimal('0.0001')
MAX_AMOUNT = Decimal('99999999999.9999')


def base_currency():
    return settings.INVENTORY_BASE_CURRENCY


class TransferType(models.TextChoices):
    FUND_TRANSFER = 'fund_transfer', 'Fund transfer'
    MONEY_TRANSFER = 'money_transfer', 'Money transfer'


class IncomeExpenseCategory(TimeStampedModel):
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        abstract = True

    def clean(self):
        super().clean()
        if isinstance(self.name, str):
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({'name': 'Enter a category name.'})

    def __str__(self):
        return self.name


class ExpenseCategory(IncomeExpenseCategory):
    class Meta(IncomeExpenseCategory.Meta):
        verbose_name_plural = 'expense categories'
        constraints = [models.UniqueConstraint(Lower(Trim('name')), name='accounting_expense_category_name_unique')]


class IncomeCategory(IncomeExpenseCategory):
    class Meta(IncomeExpenseCategory.Meta):
        verbose_name_plural = 'income categories'
        constraints = [models.UniqueConstraint(Lower(Trim('name')), name='accounting_income_category_name_unique')]


class IncomeExpense(TimeStampedModel):
    date = models.DateField(default=timezone.localdate, db_index=True)
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='%(class)ss')
    # SQLite converts stored decimal values with 15 significant digits.
    amount = models.DecimalField(max_digits=15, decimal_places=4, validators=[MinValueValidator(MIN_AMOUNT)])
    payment_account = models.ForeignKey('FinancialAccount', on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='%(class)ss_to_pay')
    is_completed = models.BooleanField(default=False, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True
        ordering = ['-date', '-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_%(class)s_amount_positive'),
            models.CheckConstraint(
                condition=(models.Q(is_completed=False, completed_at__isnull=True) |
                           models.Q(is_completed=True, completed_at__isnull=False, payment_account__isnull=False)),
                name='accounting_%(class)s_completion_valid',
            ),
        ]

    def clean(self):
        super().clean()
        previous = type(self).objects.filter(pk=self.pk).first()
        errors = {}
        if self.category_id and not self._meta.get_field('category').related_model.objects.filter(pk=self.category_id, is_active=True).exists():
            if previous is None or previous.category_id != self.category_id:
                errors['category'] = f'Choose an active {self._meta.model_name} category.'
        if self.warehouse_id:
            warehouses = self._meta.get_field('warehouse').related_model.objects
            if not warehouses.filter(pk=self.warehouse_id, is_active=True).exists():
                if previous is None or previous.warehouse_id != self.warehouse_id:
                    errors['warehouse'] = 'Choose an active warehouse.'
        if self.payment_account_id:
            from .services import payment_account_error

            error = payment_account_error(self.payment_account, self.warehouse_id)
            if error:
                errors['payment_account'] = error
        if previous and any(getattr(self, field) != getattr(previous, field)
                            for field in ('date', 'category_id', 'warehouse_id', 'amount')):
            if previous.payments.exists():
                errors['__all__'] = f'This {self._meta.model_name} has payment history and cannot be edited.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.date} - {self.category} - {self.amount}'


class Expense(IncomeExpense):
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')

    class Meta(IncomeExpense.Meta):
        indexes = [models.Index(fields=['warehouse', '-date'], name='accounting_exp_wh_date_idx')]


class Income(IncomeExpense):
    category = models.ForeignKey(IncomeCategory, on_delete=models.PROTECT, related_name='incomes')

    class Meta(IncomeExpense.Meta):
        indexes = [models.Index(fields=['warehouse', '-date'], name='accounting_inc_wh_date_idx')]


class FinancialAccount(TimeStampedModel):
    class Type(models.TextChoices):
        CASH = 'cash', 'Cash'
        BANK = 'bank', 'Bank'
        MOBILE_BANKING = 'mobile_banking', 'Mobile banking'

    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=Type.choices)
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, blank=True, null=True,
                                  related_name='financial_accounts', help_text='Leave blank for a shared account.')
    currency = models.CharField(max_length=3, default=base_currency, editable=False)
    opening_balance = models.DecimalField(max_digits=15, decimal_places=4, default=ZERO,
                                          validators=[MinValueValidator(ZERO)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(Lower(Trim('name')), name='accounting_financial_account_name_unique'),
            models.CheckConstraint(condition=models.Q(opening_balance__gte=0), name='accounting_opening_nonnegative'),
        ]

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({'name': 'Enter an account name.'})
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and any(getattr(self, field) != getattr(previous, field)
                                for field in ('type', 'warehouse_id', 'opening_balance', 'currency')):
                raise ValidationError(
                    'Account type, warehouse, currency and opening balance are set at account creation.')
        elif self.warehouse_id:
            warehouses = self._meta.get_field('warehouse').related_model.objects
            if not warehouses.filter(pk=self.warehouse_id, is_active=True).exists():
                raise ValidationError({'warehouse': 'Choose an active warehouse.'})

    def __str__(self):
        return self.name


class AccountTransfer(TimeStampedModel):
    transfer_type = models.CharField(max_length=20, choices=TransferType.choices, default=TransferType.FUND_TRANSFER)
    source_account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name='outgoing_transfers')
    destination_account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT,
                                            related_name='incoming_transfers')
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=15, decimal_places=4, validators=[MinValueValidator(MIN_AMOUNT)])
    reference = models.CharField(max_length=150, blank=True)
    request_key = models.UUIDField(default=uuid4, unique=True, editable=False)

    class Meta:
        verbose_name = 'fund transfer'
        ordering = ['-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(transfer_type__in=TransferType.values),
                                   name='accounting_transfer_valid_type'),
            models.CheckConstraint(condition=~models.Q(source_account=models.F('destination_account')),
                                   name='accounting_transfer_distinct_accounts'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_transfer_amount_positive'),
        ]

    @property
    def number(self):
        return f'TR-{self.pk}'

    def __str__(self):
        return self.number


class IncomeExpensePayment(TimeStampedModel):
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=15, decimal_places=4, validators=[MinValueValidator(MIN_AMOUNT)])
    method = models.CharField(max_length=20, choices=FinancialAccount.Type.choices)
    reference = models.CharField(max_length=150, blank=True)
    request_key = models.UUIDField(default=uuid4, unique=True, editable=False)

    class Meta:
        ordering = ['-pk']
        abstract = True

    def __str__(self):
        return self.number


class ExpensePayment(IncomeExpensePayment):
    expense = models.ForeignKey(Expense, on_delete=models.PROTECT, related_name='payments')
    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name='expense_payments')

    class Meta(IncomeExpensePayment.Meta):
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_payment_amount_positive')]

    @property
    def number(self):
        return f'EP-{self.pk}'


class IncomePayment(IncomeExpensePayment):
    income = models.ForeignKey(Income, on_delete=models.PROTECT, related_name='payments')
    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name='income_payments')

    class Meta(IncomeExpensePayment.Meta):
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_income_payment_positive')]

    @property
    def number(self):
        return f'IP-{self.pk}'


class SalesPayment(TimeStampedModel):
    sales_order = models.ForeignKey('sale.SalesOrder', on_delete=models.PROTECT, related_name='payments')
    sales_return = models.ForeignKey('sale.SalesReturn', on_delete=models.PROTECT, blank=True, null=True,
                                     related_name='refund_payments')
    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name='sales_payments')
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=15, decimal_places=4, validators=[MinValueValidator(MIN_AMOUNT)])
    method = models.CharField(max_length=20, choices=FinancialAccount.Type.choices)
    reference = models.CharField(max_length=150, blank=True)
    request_key = models.UUIDField(default=uuid4, unique=True, editable=False)

    class Meta:
        ordering = ['-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_sales_payment_amount_positive'),
            models.UniqueConstraint(fields=['sales_return', 'account'], name='accounting_refund_return_account_unique'),
        ]

    @property
    def type_label(self):
        return 'Sales refund' if self.sales_return_id else 'Sales payment'

    @property
    def number(self):
        return f'SP-{self.pk}'

    def __str__(self):
        return self.number


class PurchasePayment(TimeStampedModel):
    purchase_order = models.ForeignKey('purchase.PurchaseOrder', on_delete=models.PROTECT, related_name='payments')
    purchase_return = models.OneToOneField('purchase.PurchaseReturn', on_delete=models.PROTECT, blank=True, null=True,
                                           related_name='refund_payment')
    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name='purchase_payments')
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=15, decimal_places=4, validators=[MinValueValidator(MIN_AMOUNT)])
    method = models.CharField(max_length=20, choices=FinancialAccount.Type.choices)
    reference = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_purchase_payment_amount_positive'),
            models.UniqueConstraint(fields=['purchase_order'], condition=models.Q(purchase_return__isnull=True),
                                    name='accounting_purchase_original_payment_unique'),
        ]

    @property
    def type_label(self):
        return 'Purchase refund' if self.purchase_return_id else 'Purchase payment'

    @property
    def number(self):
        return f'PP-{self.pk}'

    def __str__(self):
        return self.number


class AccountTransaction(TimeStampedModel):
    class Direction(models.TextChoices):
        INFLOW = 'inflow', 'Inflow'
        OUTFLOW = 'outflow', 'Outflow'

    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name='transactions')
    transfer = models.ForeignKey(AccountTransfer, on_delete=models.PROTECT, related_name='transactions',
                                 blank=True, null=True)
    expense_payment = models.ForeignKey(ExpensePayment, on_delete=models.PROTECT, related_name='transactions',
                                        blank=True, null=True)
    income_payment = models.ForeignKey(IncomePayment, on_delete=models.PROTECT, related_name='transactions',
                                       blank=True, null=True)
    sales_payment = models.OneToOneField(SalesPayment, on_delete=models.PROTECT, related_name='transaction',
                                         blank=True, null=True)
    purchase_payment = models.OneToOneField(PurchasePayment, on_delete=models.PROTECT, related_name='transaction',
                                            blank=True, null=True)
    date = models.DateField()
    direction = models.CharField(max_length=7, choices=Direction.choices)
    amount = models.DecimalField(max_digits=15, decimal_places=4, validators=[MinValueValidator(MIN_AMOUNT)])
    balance_after = models.DecimalField(max_digits=15, decimal_places=4, validators=[MinValueValidator(ZERO)])

    class Meta:
        ordering = ['-pk']
        indexes = [models.Index(fields=['account', '-id'], name='accounting_tx_account_idx')]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_tx_amount_positive'),
            models.CheckConstraint(condition=models.Q(balance_after__gte=0), name='accounting_tx_balance_nonnegative'),
            models.CheckConstraint(
                condition=(
                    models.Q(transfer__isnull=False, expense_payment__isnull=True,
                             sales_payment__isnull=True, purchase_payment__isnull=True, income_payment__isnull=True) |
                    models.Q(transfer__isnull=True, expense_payment__isnull=False,
                             sales_payment__isnull=True, purchase_payment__isnull=True, income_payment__isnull=True) |
                    models.Q(transfer__isnull=True, expense_payment__isnull=True,
                             sales_payment__isnull=False, purchase_payment__isnull=True, income_payment__isnull=True) |
                    models.Q(transfer__isnull=True, expense_payment__isnull=True,
                             sales_payment__isnull=True, purchase_payment__isnull=False, income_payment__isnull=True) |
                    models.Q(transfer__isnull=True, expense_payment__isnull=True, income_payment__isnull=False,
                             sales_payment__isnull=True, purchase_payment__isnull=True)
                ),
                name='accounting_tx_one_source',
            ),
            models.UniqueConstraint(fields=['income_payment', 'direction'], name='accounting_tx_income_payment_direction'),
            models.UniqueConstraint(fields=['expense_payment', 'direction'], name='accounting_tx_payment_direction'),
            models.UniqueConstraint(fields=['transfer', 'account', 'direction'],
                                    name='accounting_tx_transfer_account_direction'),
        ]

    def __str__(self):
        return f'{self.account} · {self.get_direction_display()} · {self.amount}'
