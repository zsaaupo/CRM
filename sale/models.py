import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from common.models import (
    AppendOnlyManager, DiscountType, PaymentStatus, SettlementMode, TaxMethod, TradeStatus, TimeStampedModel,
)
from common.pricing import calculate_document_totals


MONEY_PRECISION = Decimal('0.0001')


def generate_order_number():
    return f'SO-{uuid.uuid4().hex.upper()}'


class SalesTotals:
    @property
    def subtotal(self):
        return self.calculate_totals()[0]

    @property
    def tax_total(self):
        return self.calculate_totals()[1]

    @property
    def grand_total(self):
        return self.calculate_totals()[2]

    def calculate_totals(self):
        if not self.pk:
            ship = Decimal(self.shipping_cost or 0).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
            return Decimal('0.0000'), Decimal('0.0000'), ship
        items = list(self.items.all())
        res = calculate_document_totals(
            [
                {
                    'quantity': item.quantity,
                    'unit_price': item.unit_price,
                    'discount_amount': item.discount_amount,
                    'tax_rate': item.tax_rate,
                    'tax_method': getattr(item, 'tax_method', TaxMethod.EXCLUSIVE),
                }
                for item in items
            ],
            order_discount_type=getattr(self, 'order_discount_type', DiscountType.FLAT),
            order_discount_value=getattr(self, 'order_discount_value', Decimal('0')),
            order_tax_rate=getattr(self, 'order_tax_rate', Decimal('0')),
            shipping_cost=self.shipping_cost,
            digits=4,
        )
        return res.taxable_subtotal, res.line_tax_total + res.order_tax_amount, res.grand_total


class SalesLineAmounts:
    @property
    def net_amount(self):
        return self.calculate_amounts()[0]

    @property
    def tax_amount(self):
        return self.calculate_amounts()[1]

    @property
    def line_total(self):
        return self.calculate_amounts()[2]

    @property
    def returnable_total(self):
        net, tax, _ = self.calculate_amounts()
        return (
            net
            - Decimal(getattr(self, 'allocated_order_discount', Decimal('0')) or 0)
            + tax
            + Decimal(getattr(self, 'allocated_order_tax', Decimal('0')) or 0)
        )

    def calculate_amounts(self):
        with localcontext() as context:
            context.prec = 50
            raw_net = Decimal(self.quantity) * Decimal(self.unit_price) - Decimal(self.discount_amount)
            method = getattr(self, 'tax_method', TaxMethod.EXCLUSIVE) or TaxMethod.EXCLUSIVE
            rate = Decimal(self.tax_rate)
            if method == TaxMethod.INCLUSIVE and rate > 0:
                net_q = raw_net.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
                base = (raw_net / (Decimal('1') + rate / Decimal('100'))).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
                return base, net_q - base, net_q
            net = raw_net.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
            tax = (net * rate / Decimal('100')).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
            return net, tax, net + tax

    def clean(self):
        super().clean()
        if self.product_id and self.unit_id:
            try:
                product = self.product
                unit = self.unit
            except (self._meta.get_field('product').related_model.DoesNotExist,
                    self._meta.get_field('unit').related_model.DoesNotExist):
                return
            from inventory.models import check_units_convertible
            check_units_convertible(unit, product.unit)

    def sync_base_quantity(self):
        if self.product_id and self.unit_id:
            from inventory.models import check_units_convertible
            factor = check_units_convertible(self.unit, self.product.unit)
            self.unit_conversion_factor = factor
            self.base_quantity = (Decimal(self.quantity) * factor).quantize(Decimal('0.000001'))
        elif self.base_quantity is None:
            self.base_quantity = self.quantity


class SalesOrder(SalesTotals, TimeStampedModel):
    order_number = models.CharField(max_length=50, unique=True, default=generate_order_number, editable=False)
    customer = models.ForeignKey('people.Party', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='sales_orders')
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='sales_orders')
    payment_account = models.ForeignKey('accounting.FinancialAccount', on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='sales_to_receive')
    order_date = models.DateField(default=timezone.localdate)
    expected_delivery_date = models.DateField(null=True, blank=True)
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT,
                                 related_name='sales_orders')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8,
                                        validators=[MinValueValidator(Decimal('0.00000001'))],
                                        help_text='Snapshot: base-currency units per one order-currency unit.')
    status = models.ForeignKey(TradeStatus, to_field='code', on_delete=models.PROTECT,
                               default='draft', related_name='sales_orders')
    customer_reference = models.CharField(max_length=100, blank=True)
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    shipping_cost = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                        validators=[MinValueValidator(0)])
    order_discount_type = models.CharField(max_length=16, choices=DiscountType.choices, default=DiscountType.FLAT)
    order_discount_value = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                               validators=[MinValueValidator(0)])
    order_discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    order_tax_rule = models.ForeignKey(
        'inventory.TaxRule', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    order_tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'),
                                         validators=[MinValueValidator(0), MaxValueValidator(100)])
    order_tax_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    biller = models.ForeignKey('people.Biller', on_delete=models.PROTECT, null=True, blank=True,
                               related_name='sales_orders')
    biller_name_snapshot = models.CharField(max_length=200, blank=True)
    attachment = models.FileField(upload_to='sale_attachments/', blank=True)
    staff_note = models.TextField(blank=True)
    sale_note = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    fulfilled_at = models.DateTimeField(null=True, blank=True, editable=False)
    is_completed = models.BooleanField(default=False, editable=False)
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True,
    )
    settlement_mode = models.CharField(
        max_length=16, choices=SettlementMode.choices, default=SettlementMode.AUTO_FULL,
    )
    recognized_total_base = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    register_session = models.ForeignKey(
        'CashRegisterSession', on_delete=models.PROTECT, null=True, blank=True, related_name='sales_orders',
    )
    channel = models.CharField(
        max_length=12, choices=[('web', 'Web'), ('pos', 'POS')], default='web', db_index=True,
    )
    coupon_code = models.CharField(max_length=64, blank=True)
    coupon_discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))

    class Meta:
        ordering = ['-order_date', '-pk']
        verbose_name = 'sales order'
        constraints = [
            models.CheckConstraint(condition=Q(exchange_rate__gt=0), name='sale_order_rate_positive'),
            models.CheckConstraint(condition=Q(shipping_cost__gte=0), name='sale_order_shipping_nonneg'),
            models.CheckConstraint(condition=Q(expected_delivery_date__isnull=True) | Q(expected_delivery_date__gte=F('order_date')),
                                   name='sale_order_dates_valid'),
        ]

    def clean(self):
        super().clean()
        from django.conf import settings

        errors = {}
        if self.customer_id:
            party_model = self._meta.get_field('customer').related_model
            eligible = party_model.objects.filter(pk=self.customer_id, is_customer=True).exists()
            # Keep an existing historical link usable when only other order fields change.
            unchanged = not eligible and self.pk is not None and type(self).objects.filter(
                pk=self.pk, customer_id=self.customer_id,
            ).exists()
            if not eligible and not unchanged:
                errors['customer'] = 'Choose a non-deleted party marked as a customer.'
        if self.currency_id == settings.INVENTORY_BASE_CURRENCY and self.exchange_rate != Decimal('1'):
            errors['exchange_rate'] = 'The base currency exchange rate must be 1.'
        if self.payment_account_id:
            from accounting.services import payment_account_error

            error = payment_account_error(self.payment_account, self.warehouse_id)
            if error:
                errors['payment_account'] = error
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.order_number


class SalesOrderItem(SalesLineAmounts, TimeStampedModel):
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='items')
    line_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='sales_order_items')
    description = models.TextField(blank=True, help_text='Description snapshot for this order line.')
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='sales_order_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6,
                                   validators=[MinValueValidator(Decimal('0.000001'))])
    unit_conversion_factor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'))
    base_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    batch = models.ForeignKey(
        'warehouse.StockBatch', on_delete=models.PROTECT, null=True, blank=True, related_name='sale_items',
    )
    serial_numbers = models.JSONField(default=list, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(0)])
    discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                          validators=[MinValueValidator(0)], help_text='Discount for the entire line.')
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'),
                                   validators=[MinValueValidator(0), MaxValueValidator(100)])
    tax_method = models.CharField(max_length=16, choices=TaxMethod.choices, default=TaxMethod.EXCLUSIVE)
    allocated_order_discount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    allocated_order_tax = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    rounding_residual = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    unit_cost_base = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    applied_discount_rule_id = models.BigIntegerField(null=True, blank=True)
    applied_discount_name = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['line_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['sales_order', 'line_number'], name='sales_order_line_unique'),
            models.CheckConstraint(condition=Q(line_number__gt=0), name='sale_item_line_positive'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='sale_item_quantity_positive'),
            models.CheckConstraint(condition=Q(unit_price__gte=0, discount_amount__gte=0), name='sale_item_prices_nonneg'),
            models.CheckConstraint(condition=Q(discount_amount__lte=F('quantity') * F('unit_price')),
                                   name='sale_item_discount_bounds'),
            models.CheckConstraint(condition=Q(tax_rate__gte=0, tax_rate__lte=100), name='sale_item_tax_bounds'),
        ]

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is None or any(f in update_fields for f in ('quantity', 'unit', 'product')):
            self.sync_base_quantity()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.sales_order.order_number} / {self.line_number}'


def generate_return_number():
    return f'SR-{uuid.uuid4().hex.upper()}'


class SalesReturn(TimeStampedModel):
    return_number = models.CharField(max_length=50, unique=True, default=generate_return_number, editable=False)
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='returns')
    return_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    is_completed = models.BooleanField(default=False, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-return_date', '-pk']
        constraints = [models.CheckConstraint(
            condition=Q(is_completed=False, completed_at__isnull=True) | Q(is_completed=True, completed_at__isnull=False),
            name='sale_return_completion_valid',
        )]

    def clean(self):
        super().clean()
        order = SalesOrder.objects.filter(pk=self.sales_order_id).first()
        if order is None:
            return
        if not order.is_completed or order.warehouse_id is None:
            raise ValidationError({'sales_order': 'Choose a completed sale with a warehouse.'})
        if self.return_date and self.return_date < order.order_date:
            raise ValidationError({'return_date': 'The return date cannot be before the sales order date.'})

    def __str__(self):
        return self.return_number


class SalesReturnItem(TimeStampedModel):
    sales_return = models.ForeignKey(SalesReturn, on_delete=models.PROTECT, related_name='items')
    sales_order_item = models.ForeignKey(SalesOrderItem, on_delete=models.PROTECT, related_name='return_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6,
                                   validators=[MinValueValidator(Decimal('0.000001'))])

    class Meta:
        ordering = ['sales_order_item__line_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['sales_return', 'sales_order_item'], name='sale_return_order_item_unique'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='sale_return_item_quantity_positive'),
        ]

    def clean(self):
        super().clean()
        if self.sales_return_id and self.sales_order_item_id:
            item = SalesOrderItem.objects.filter(pk=self.sales_order_item_id).first()
            record = SalesReturn.objects.filter(pk=self.sales_return_id).first()
            if item and record and item.sales_order_id != record.sales_order_id:
                raise ValidationError({'sales_order_item': 'Choose an item from the original sales order.'})


def generate_quotation_number():
    return f'SQ-{uuid.uuid4().hex.upper()}'


def quotation_valid_until(order_date=None):
    return (order_date or timezone.localdate()) + timedelta(days=30)


class SalesQuotation(SalesTotals, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ISSUED = 'issued', 'Issued'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'
        SUPERSEDED = 'superseded', 'Superseded'
        CONVERTED = 'converted', 'Converted'

    quotation_number = models.CharField(max_length=50, unique=True, default=generate_quotation_number, editable=False)
    customer = models.ForeignKey('people.Party', on_delete=models.PROTECT, related_name='sales_quotations')
    supplier = models.ForeignKey(
        'people.Party', on_delete=models.PROTECT, null=True, blank=True, related_name='supplier_quotations',
    )
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='sales_quotations')
    order_date = models.DateField(default=timezone.localdate, verbose_name='quotation date')
    valid_until = models.DateField(default=quotation_valid_until)
    expected_delivery_date = models.DateField(null=True, blank=True)
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT,
                                 related_name='sales_quotations')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8,
                                        validators=[MinValueValidator(Decimal('0.00000001'))])
    customer_reference = models.CharField(max_length=100, blank=True)
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    shipping_cost = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                        validators=[MinValueValidator(0)])
    order_discount_type = models.CharField(max_length=16, choices=DiscountType.choices, default=DiscountType.FLAT)
    order_discount_value = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                               validators=[MinValueValidator(0)])
    order_discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    order_tax_rule = models.ForeignKey(
        'inventory.TaxRule', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    order_tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'),
                                         validators=[MinValueValidator(0), MaxValueValidator(100)])
    order_tax_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    biller = models.ForeignKey('people.Biller', on_delete=models.PROTECT, null=True, blank=True,
                               related_name='sales_quotations')
    biller_name_snapshot = models.CharField(max_length=200, blank=True)
    attachment = models.FileField(upload_to='quotation_attachments/', blank=True)
    staff_note = models.TextField(blank=True)
    sale_note = models.TextField(blank=True)
    notes = models.TextField(blank=True, help_text='Internal notes.')
    terms = models.TextField(blank=True, help_text='Terms printed on the customer quotation.')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, editable=False)
    issued_at = models.DateTimeField(null=True, blank=True, editable=False)
    issued_by = models.CharField(max_length=255, blank=True, editable=False)
    responded_at = models.DateTimeField(null=True, blank=True, editable=False)
    responded_by = models.CharField(max_length=255, blank=True, editable=False)
    issued_document = models.JSONField(default=dict, editable=False)
    revised_from = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT,
                                        related_name='revision', editable=False)
    sales_order = models.OneToOneField(SalesOrder, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name='source_quotation', editable=False)

    class Meta:
        ordering = ['-order_date', '-pk']
        permissions = [
            ('issue_salesquotation', 'Can issue sales quotations'),
            ('respond_salesquotation', 'Can record responses to sales quotations'),
            ('convert_salesquotation', 'Can convert sales quotations to orders'),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(exchange_rate__gt=0, shipping_cost__gte=0), name='sale_quote_amounts_valid'),
            models.CheckConstraint(condition=Q(valid_until__gte=F('order_date')), name='sale_quote_validity_dates'),
            models.CheckConstraint(condition=Q(expected_delivery_date__isnull=True) | Q(expected_delivery_date__gte=F('order_date')),
                                   name='sale_quote_delivery_dates'),
            models.CheckConstraint(condition=Q(status__in=['draft', 'issued', 'accepted', 'declined', 'superseded', 'converted']),
                                   name='sale_quote_status_valid'),
            models.CheckConstraint(condition=Q(status='draft', issued_at__isnull=True) |
                                   (~Q(status='draft') & Q(issued_at__isnull=False)), name='sale_quote_issue_state'),
            models.CheckConstraint(condition=Q(status='converted', sales_order__isnull=False) |
                                   (~Q(status='converted') & Q(sales_order__isnull=True)), name='sale_quote_order_state'),
        ]

    @property
    def is_expired(self):
        return self.status == self.Status.ISSUED and self.valid_until < timezone.localdate()

    def clean(self):
        super().clean()
        from django.conf import settings

        errors = {}
        if self.customer_id:
            parties = self._meta.get_field('customer').related_model.objects
            if not parties.filter(pk=self.customer_id, is_customer=True, is_active=True).exists():
                errors['customer'] = 'Choose an active customer.'
        if self.supplier_id:
            parties = self._meta.get_field('supplier').related_model.objects
            if not parties.filter(pk=self.supplier_id, is_supplier=True, is_active=True).exists():
                errors['supplier'] = 'Choose an active supplier.'
        if self.warehouse_id:
            warehouses = self._meta.get_field('warehouse').related_model.objects
            if not warehouses.filter(pk=self.warehouse_id, is_active=True).exists():
                errors['warehouse'] = 'Choose an active warehouse.'
        if self.currency_id == settings.INVENTORY_BASE_CURRENCY and self.exchange_rate != Decimal('1'):
            errors['exchange_rate'] = 'The base currency exchange rate must be 1.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.quotation_number


class SalesQuotationItem(SalesLineAmounts, TimeStampedModel):
    quotation = models.ForeignKey(SalesQuotation, on_delete=models.PROTECT, related_name='items')
    line_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='sales_quotation_items')
    description = models.TextField(blank=True)
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='sales_quotation_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))])
    unit_conversion_factor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'))
    base_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(0)])
    discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                          validators=[MinValueValidator(0)], help_text='Discount for the entire line.')
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'),
                                   validators=[MinValueValidator(0), MaxValueValidator(100)])
    tax_method = models.CharField(max_length=16, choices=TaxMethod.choices, default=TaxMethod.EXCLUSIVE)
    allocated_order_discount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    allocated_order_tax = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    rounding_residual = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))

    class Meta:
        ordering = ['line_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['quotation', 'line_number'], name='sale_quote_line_unique'),
            models.CheckConstraint(condition=Q(line_number__gt=0, quantity__gt=0), name='sale_quote_line_quantity_valid'),
            models.CheckConstraint(condition=Q(unit_price__gte=0, discount_amount__gte=0,
                                               discount_amount__lte=F('quantity') * F('unit_price')),
                                   name='sale_quote_line_prices_valid'),
            models.CheckConstraint(condition=Q(tax_rate__gte=0, tax_rate__lte=100), name='sale_quote_line_tax_valid'),
        ]

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is None or any(f in update_fields for f in ('quantity', 'unit', 'product')):
            self.sync_base_quantity()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.quotation} / {self.line_number}'


# ---------------------------------------------------------------------------
# M3: QUO-01 Quotation Conversion, POS-01 Cash Register, PRICE-01 Rules/Stored Value, SHIP-01 Delivery/Challan
# ---------------------------------------------------------------------------


class QuotationConversion(TimeStampedModel):
    class Kind(models.TextChoices):
        SALE = 'sale', 'Sale'
        PURCHASE = 'purchase', 'Purchase'

    quotation_revision = models.ForeignKey(SalesQuotation, on_delete=models.PROTECT, related_name='conversions')
    kind = models.CharField(max_length=16, choices=Kind.choices)
    target_type = models.CharField(max_length=64)
    target_id = models.BigIntegerField()
    request_key = models.UUIDField(default=uuid.uuid4)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['quotation_revision', 'kind'], name='sale_quote_conversion_kind_unique'),
        ]

    def __str__(self):
        return f'{self.quotation_revision.quotation_number} -> {self.kind}#{self.target_id}'


class CashRegisterSession(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CLOSED = 'closed', 'Closed'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='register_sessions')
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='register_sessions')
    opened_at = models.DateTimeField(default=timezone.now, editable=False)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    opening_cash = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'), validators=[MinValueValidator(0)])
    closing_cash_expected = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    closing_cash_actual = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    variance = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-opened_at', '-pk']
        permissions = [
            ('use_pos', 'Can use POS terminal'),
            ('open_register', 'Can open cash register'),
            ('close_register', 'Can close cash register'),
            ('override_price', 'Can override POS line prices'),
            ('convert_quotation_purchase', 'Can convert quotation to purchase order'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'warehouse'],
                condition=Q(status='open'),
                name='sale_one_open_register_per_user_wh',
            ),
        ]

    def __str__(self):
        return f'Register#{self.pk} ({self.user_id}@{self.warehouse_id} {self.status})'


class RegisterMovement(TimeStampedModel):
    class Kind(models.TextChoices):
        CASH_IN = 'cash_in', 'Cash in'
        CASH_OUT = 'cash_out', 'Cash out'

    session = models.ForeignKey(CashRegisterSession, on_delete=models.PROTECT, related_name='movements')
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    note = models.TextField(blank=True)
    account = models.ForeignKey(
        'accounting.FinancialAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='register_movements',
    )
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        ordering = ['-pk']

    def __str__(self):
        return f'{self.session_id}:{self.kind}:{self.amount}'


class PosDraft(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pos_drafts')
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='pos_drafts')
    customer = models.ForeignKey('people.Party', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    cart = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-updated_at', '-pk']

    def __str__(self):
        return f'POS Draft #{self.pk}'


class DiscountPlan(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)
    customers = models.ManyToManyField('people.Party', blank=True, related_name='discount_plans_m2m')
    customer_groups = models.ManyToManyField('people.CustomerGroup', blank=True, related_name='discount_plans')

    class Meta:
        ordering = ['name', 'pk']
        permissions = [('manage_discount_plan', 'Can manage discount plans')]

    def __str__(self):
        return self.name


class Discount(TimeStampedModel):
    class AppliesTo(models.TextChoices):
        ALL = 'all', 'All products'
        PRODUCTS = 'products', 'Specific products'
        CATEGORIES = 'categories', 'Specific categories'

    plan = models.ForeignKey(DiscountPlan, on_delete=models.CASCADE, related_name='rules')
    name = models.CharField(max_length=150)
    valid_from = models.DateField()
    valid_till = models.DateField()
    discount_type = models.CharField(max_length=16, choices=DiscountType.choices, default=DiscountType.PERCENTAGE)
    value = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(0)])
    minimum_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'), validators=[MinValueValidator(0)])
    maximum_qty = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    days_of_week = models.JSONField(default=list, blank=True, help_text='0=Mon..6=Sun; empty means every day.')
    applies_to = models.CharField(max_length=16, choices=AppliesTo.choices, default=AppliesTo.ALL)
    products = models.ManyToManyField('inventory.Product', blank=True, related_name='discount_rules')
    categories = models.ManyToManyField('inventory.ProductCategory', blank=True, related_name='discount_rules')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['pk']

    def clean(self):
        super().clean()
        if self.valid_from and self.valid_till and self.valid_till < self.valid_from:
            raise ValidationError({'valid_till': 'End date cannot be before start date.'})

    def __str__(self):
        return self.name


class Coupon(TimeStampedModel):
    class Type(models.TextChoices):
        FIXED = 'fixed', 'Fixed'
        PERCENTAGE = 'percentage', 'Percentage'

    code = models.CharField(max_length=64, unique=True)
    coupon_type = models.CharField(max_length=16, choices=Type.choices, default=Type.FIXED)
    amount = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    minimum_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'), validators=[MinValueValidator(0)])
    quantity = models.PositiveIntegerField(default=1)
    used_count = models.PositiveIntegerField(default=0)
    expired_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code', 'pk']
        permissions = [('manage_coupon', 'Can manage coupons')]

    def clean(self):
        super().clean()
        self.code = self.code.strip().upper()

    def __str__(self):
        return self.code


class CouponRedemption(TimeStampedModel):
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name='redemptions')
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='coupon_redemptions')
    customer = models.ForeignKey('people.Party', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    amount = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['coupon', 'sales_order'], name='sale_coupon_order_unique'),
        ]

    def __str__(self):
        return f'{self.coupon.code} on {self.sales_order_id}'


class GiftCard(TimeStampedModel):
    card_no = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    balance = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    customer = models.ForeignKey('people.Party', on_delete=models.PROTECT, null=True, blank=True, related_name='gift_cards')
    expired_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        permissions = [('manage_gift_card', 'Can manage gift cards')]

    def __str__(self):
        return f'{self.card_no} ({self.balance})'


class GiftCardEntry(TimeStampedModel):
    class Kind(models.TextChoices):
        ISSUE = 'issue', 'Issue'
        REDEEM = 'redeem', 'Redeem'
        ADJUST = 'adjust', 'Adjust'
        REVERSAL = 'reversal', 'Reversal'
        RECHARGE = 'recharge', 'Recharge'

    gift_card = models.ForeignKey(GiftCard, on_delete=models.PROTECT, related_name='entries')
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=4)
    sales_payment = models.ForeignKey(
        'accounting.SalesPayment', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    note = models.TextField(blank=True)
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    objects = AppendOnlyManager()

    class Meta:
        ordering = ['pk']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('GiftCardEntry is append-only.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('GiftCardEntry is append-only.')


def _deliv_no():
    return f'DL-{uuid.uuid4().hex[:12].upper()}'


def _slip_no():
    return f'PS-{uuid.uuid4().hex[:12].upper()}'


def _challan_no():
    return f'CH-{uuid.uuid4().hex[:12].upper()}'


class Courier(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class Delivery(TimeStampedModel):
    class Status(models.TextChoices):
        PACKING = 'packing', 'Packing'
        DELIVERING = 'delivering', 'Delivering'
        DELIVERED = 'delivered', 'Delivered'

    reference_number = models.CharField(max_length=50, unique=True, default=_deliv_no, editable=False)
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='deliveries')
    courier = models.ForeignKey(Courier, on_delete=models.PROTECT, null=True, blank=True, related_name='deliveries')
    delivered_by = models.CharField(max_length=150, blank=True)
    received_by = models.CharField(max_length=150, blank=True)
    customer = models.ForeignKey('people.Party', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    address = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PACKING)
    attachment = models.FileField(upload_to='delivery_attachments/', blank=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return self.reference_number


class DeliveryItem(TimeStampedModel):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name='items')
    sales_order_item = models.ForeignKey(SalesOrderItem, on_delete=models.PROTECT, related_name='delivery_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))])

    class Meta:
        ordering = ['pk']


class PackingSlip(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        PACKED = 'packed', 'Packed'
        ON_CHALLAN = 'on_challan', 'On challan'
        CLOSED = 'closed', 'Closed'

    reference_number = models.CharField(max_length=50, unique=True, default=_slip_no, editable=False)
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='packing_slips')
    delivery = models.ForeignKey(Delivery, on_delete=models.PROTECT, null=True, blank=True, related_name='packing_slips')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ['-created_at', '-pk']
        permissions = [('manage_packing_slip', 'Can manage packing slips')]

    def __str__(self):
        return self.reference_number


class PackingSlipItem(TimeStampedModel):
    packing_slip = models.ForeignKey(PackingSlip, on_delete=models.CASCADE, related_name='items')
    sales_order_item = models.ForeignKey(SalesOrderItem, on_delete=models.PROTECT, related_name='packing_slip_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))])

    class Meta:
        ordering = ['pk']


class Challan(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        DISPATCHED = 'dispatched', 'Dispatched'
        CLOSED = 'closed', 'Closed'

    reference_number = models.CharField(max_length=50, unique=True, default=_challan_no, editable=False)
    courier = models.ForeignKey(Courier, on_delete=models.PROTECT, related_name='challans')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='created_challans',
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='closed_challans',
    )
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closing_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))

    class Meta:
        ordering = ['-created_at', '-pk']
        permissions = [
            ('issue_challan', 'Can issue challans'),
            ('close_challan', 'Can close challans'),
        ]

    def __str__(self):
        return self.reference_number


class ChallanItem(TimeStampedModel):
    challan = models.ForeignKey(Challan, on_delete=models.CASCADE, related_name='items')
    packing_slip = models.ForeignKey(PackingSlip, on_delete=models.PROTECT, null=True, blank=True, related_name='challan_links')
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, null=True, blank=True, related_name='challan_links')
    is_open = models.BooleanField(default=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['packing_slip'],
                condition=Q(is_open=True, packing_slip__isnull=False),
                name='sale_slip_one_open_challan',
            ),
        ]
