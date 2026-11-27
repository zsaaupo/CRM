import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext

from django.core.exceptions import ValidationError
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from accounting.models import base_currency
from common.models import TradeStatus, TimeStampedModel
from inventory.models import RAW_MATERIAL_TYPE
from .instrument_models import Coupon, CouponRedemption, GiftCard, GiftCardTransaction


MONEY_PRECISION = Decimal('0.0001')


def generate_order_number():
    return f'SO-{uuid.uuid4().hex.upper()}'


def generate_chalan_number():
    return f'CH-{uuid.uuid4().hex.upper()}'


def generate_packing_slip_number():
    return f'PS-{uuid.uuid4().hex.upper()}'


def validate_sales_product_line(line):
    if line.product_id and line.unit_id:
        try:
            product = line.product
        except line._meta.get_field('product').related_model.DoesNotExist:
            return
        if product.product_type_id == RAW_MATERIAL_TYPE:
            raise ValidationError({'product': 'Choose a sellable product.'})
        if line.unit_id != product.unit_id:
            raise ValidationError({'unit': 'Use the product unit. Unit conversion is not supported yet.'})


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

    def calculate_totals(self, *, items=None):
        if items is None:
            items = self.items.all() if self.pk else ()
        with localcontext() as context:
            context.prec = 50
            subtotal = tax = Decimal('0.0000')
            for item in items:
                net, item_tax, _ = item.calculate_amounts()
                subtotal += net
                tax += item_tax
            return subtotal, tax, subtotal + tax + Decimal(self.shipping_cost)


class SalesLineAmounts:
    amount_precision = MONEY_PRECISION
    @property
    def net_amount(self):
        return self.calculate_amounts()[0]

    @property
    def tax_amount(self):
        return self.calculate_amounts()[1]

    @property
    def line_total(self):
        return self.calculate_amounts()[2]

    def calculate_amounts(self):
        with localcontext() as context:
            context.prec = 50
            net = (Decimal(self.quantity) * Decimal(self.unit_price) - Decimal(self.discount_amount)).quantize(
                self.amount_precision, rounding=ROUND_HALF_UP)
            tax = (net * Decimal(self.tax_rate) / Decimal('100')).quantize(self.amount_precision, rounding=ROUND_HALF_UP)
            return net, tax, net + tax

    def clean(self):
        super().clean()
        validate_sales_product_line(self)


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
    notes = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    fulfilled_at = models.DateTimeField(null=True, blank=True, editable=False)
    is_completed = models.BooleanField(default=False, editable=False)

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
    pos_line = models.BooleanField(default=False, editable=False)

    @property
    def amount_precision(self):
        return Decimal('0.01') if self.pos_line else MONEY_PRECISION

    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name='items')
    line_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='sales_order_items')
    description = models.TextField(blank=True, help_text='Description snapshot for this order line.')
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='sales_order_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6,
                                   validators=[MinValueValidator(Decimal('0.000001'))])
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(0)])
    discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                          validators=[MinValueValidator(0)], help_text='Discount for the entire line.')
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'),
                                   validators=[MinValueValidator(0), MaxValueValidator(100)])

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

    def __str__(self):
        return f'{self.sales_order.order_number} / {self.line_number}'


class Chalan(SalesTotals, TimeStampedModel):
    chalan_number = models.CharField(max_length=50, unique=True, default=generate_chalan_number, editable=False)
    customer = models.ForeignKey('people.Party', on_delete=models.PROTECT, related_name='chalans')
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='chalans')
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT,
                                 related_name='chalans', default=base_currency)
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal('1'),
                                        validators=[MinValueValidator(Decimal('0.00000001'))],
                                        help_text='Snapshot: base-currency units per one Chalan-currency unit.')
    shipping_cost = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                        validators=[MinValueValidator(0)])
    chalan_date = models.DateField(default=timezone.localdate)
    expected_delivery_date = models.DateField(null=True, blank=True)
    customer_reference = models.CharField(max_length=100, blank=True)
    recipient_name = models.CharField(max_length=150, blank=True)
    recipient_phone = models.CharField(max_length=32, blank=True)
    shipping_address = models.TextField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-chalan_date', '-pk']
        constraints = [
            models.CheckConstraint(condition=Q(exchange_rate__gt=0), name='sale_chalan_rate_positive'),
            models.CheckConstraint(condition=Q(shipping_cost__gte=0), name='sale_chalan_shipping_nonneg'),
            models.CheckConstraint(
                condition=Q(expected_delivery_date__isnull=True) | Q(expected_delivery_date__gte=F('chalan_date')),
                name='sale_chalan_dates_valid',
                violation_error_message='Expected delivery date must be on or after the Chalan date.',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.customer_id:
            parties = self._meta.get_field('customer').related_model.objects
            if not parties.filter(pk=self.customer_id, is_customer=True).exists() and not self._keeps('customer'):
                errors['customer'] = 'Choose a non-deleted party marked as a customer.'
        if self.warehouse_id:
            warehouses = self._meta.get_field('warehouse').related_model.objects
            if not warehouses.filter(pk=self.warehouse_id, is_active=True).exists() and not self._keeps('warehouse'):
                errors['warehouse'] = 'Choose an active warehouse.'
        if self.currency_id == base_currency() and self.exchange_rate != Decimal('1'):
            errors['exchange_rate'] = 'The base currency exchange rate must be 1.'
        if errors:
            raise ValidationError(errors)

    def _keeps(self, field):
        key = f'{field}_id'
        return self.pk is not None and type(self).objects.filter(pk=self.pk, **{key: getattr(self, key)}).exists()

    @property
    def is_delivered(self):
        delivery = getattr(self, 'delivery', None)
        return delivery is not None and delivery.status == DeliveryStatus.DELIVERED

    @property
    def stock_in_transit(self):
        delivery = getattr(self, 'delivery', None)
        return (delivery is not None and delivery.stock_deducted_at is not None
                and delivery.status in (DeliveryStatus.HANDED_OVER, DeliveryStatus.IN_TRANSIT, DeliveryStatus.FAILED))

    @property
    def is_delete_locked(self):
        return self.is_delivered or self.stock_in_transit

    def __str__(self):
        return self.chalan_number


class ChalanItem(SalesLineAmounts, TimeStampedModel):
    chalan = models.ForeignKey(Chalan, on_delete=models.PROTECT, related_name='items')
    line_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='chalan_items')
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='chalan_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))])
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                     validators=[MinValueValidator(0)])
    discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                          validators=[MinValueValidator(0)], help_text='Discount for the entire line.')
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'),
                                   validators=[MinValueValidator(0), MaxValueValidator(100)])
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['line_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['chalan', 'line_number'], name='sale_chalan_line_unique'),
            models.CheckConstraint(condition=Q(line_number__gt=0), name='sale_chalan_line_positive'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='sale_chalan_quantity_positive'),
            models.CheckConstraint(condition=Q(unit_price__gte=0, discount_amount__gte=0),
                                   name='sale_chalan_item_prices_nonneg'),
            models.CheckConstraint(condition=Q(discount_amount__lte=F('quantity') * F('unit_price')),
                                   name='sale_chalan_item_discount_bounds'),
            models.CheckConstraint(condition=Q(tax_rate__gte=0, tax_rate__lte=100), name='sale_chalan_item_tax_bounds'),
        ]

    def __str__(self):
        return f'{self.chalan.chalan_number} / {self.line_number}'


class PackingSlip(TimeStampedModel):
    chalan = models.OneToOneField(Chalan, on_delete=models.PROTECT, related_name='packing_slip')
    slip_number = models.CharField(max_length=50, unique=True, default=generate_packing_slip_number, editable=False)

    class Meta:
        ordering = ['-created_at', '-pk']
        default_permissions = ('view', 'add')

    def __str__(self):
        return self.slip_number


class Courier(TimeStampedModel):
    name = models.CharField(max_length=150)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        default_permissions = ('view', 'add', 'change')
        constraints = [models.UniqueConstraint(Lower(Trim('name')), name='sale_courier_name_unique',
                                               violation_error_message='A courier with this name already exists.')]

    def clean(self):
        super().clean()
        self.name = (self.name or '').strip()
        if not self.name:
            raise ValidationError({'name': 'Enter a courier name.'})

    def __str__(self):
        return self.name


class DeliveryStatus(models.TextChoices):
    ASSIGNED = 'assigned', 'Assigned'
    HANDED_OVER = 'handed_over', 'Handed over'
    IN_TRANSIT = 'in_transit', 'In transit'
    DELIVERED = 'delivered', 'Delivered'
    FAILED = 'failed', 'Failed delivery'
    RETURNED = 'returned', 'Returned'
    CANCELLED = 'cancelled', 'Cancelled'


class Delivery(TimeStampedModel):
    chalan = models.OneToOneField(Chalan, on_delete=models.PROTECT, related_name='delivery')
    courier = models.ForeignKey(Courier, on_delete=models.PROTECT, related_name='deliveries')
    tracking_number = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, choices=DeliveryStatus.choices, default=DeliveryStatus.ASSIGNED,
                              editable=False)
    stock_deducted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-updated_at', '-pk']
        default_permissions = ('view', 'add', 'change')
        constraints = [models.CheckConstraint(condition=Q(status__in=DeliveryStatus.values),
                                              name='sale_delivery_status_valid')]

    @property
    def chalan_number(self):
        return self.chalan.chalan_number

    def __str__(self):
        return self.chalan_number


class DeliveryStatusUpdate(models.Model):
    delivery = models.ForeignKey(Delivery, on_delete=models.PROTECT, related_name='status_updates')
    status = models.CharField(max_length=12, choices=DeliveryStatus.choices)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    create_by = models.CharField(max_length=255)

    class Meta:
        ordering = ['-created_at', '-pk']
        default_permissions = ()
        constraints = [models.CheckConstraint(condition=Q(status__in=DeliveryStatus.values),
                                              name='sale_delivery_update_status_valid')]


def generate_return_number():
    return f'SR-{uuid.uuid4().hex.upper()}'


class POSTicket(models.Model):
    key = models.UUIDField(primary_key=True)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT)
    order = models.OneToOneField(SalesOrder, null=True, on_delete=models.PROTECT, related_name='pos_ticket')
    revision = models.PositiveIntegerField(default=0)
    cancelled = models.BooleanField(default=False)
    cart = models.JSONField(default=dict)
    receipt = models.JSONField(default=dict)
    request_digest = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


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
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(0)])
    discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                          validators=[MinValueValidator(0)], help_text='Discount for the entire line.')
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'),
                                   validators=[MinValueValidator(0), MaxValueValidator(100)])

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

    def __str__(self):
        return f'{self.quotation} / {self.line_number}'
