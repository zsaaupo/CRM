import uuid
from decimal import Decimal, ROUND_HALF_UP, localcontext

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from common.models import DiscountType, PaymentStatus, SettlementMode, TaxMethod, TradeStatus, TimeStampedModel
from common.pricing import calculate_document_totals


MONEY_PRECISION = Decimal('0.0001')


def generate_order_number():
    return f'PO-{uuid.uuid4().hex.upper()}'


def generate_return_number():
    return f'PR-{uuid.uuid4().hex.upper()}'


def generate_receipt_number():
    return f'REC-{uuid.uuid4().hex.upper()}'


class PurchaseOrder(TimeStampedModel):
    class ReceiptState(models.TextChoices):
        ORDERED = 'ordered', 'Ordered'
        PENDING = 'pending', 'Pending'
        PARTIAL = 'partial', 'Partial'
        RECEIVED = 'received', 'Received'
    order_number = models.CharField(max_length=50, unique=True, default=generate_order_number, editable=False)
    supplier = models.ForeignKey('people.Party', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='purchase_orders')
    order_date = models.DateField(default=timezone.localdate)
    expected_date = models.DateField(null=True, blank=True)
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='purchase_orders')
    payment_account = models.ForeignKey('accounting.FinancialAccount', on_delete=models.PROTECT, null=True,
                                        blank=True, related_name='purchases_to_pay')
    is_received = models.BooleanField(default=False, editable=False)
    receipt_state = models.CharField(
        max_length=16, choices=ReceiptState.choices, default=ReceiptState.PENDING, db_index=True,
    )
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True,
    )
    settlement_mode = models.CharField(
        max_length=16, choices=SettlementMode.choices, default=SettlementMode.AUTO_FULL,
    )
    recognized_total_base = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT,
                                 related_name='purchase_orders')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8,
                                        validators=[MinValueValidator(Decimal('0.00000001'))],
                                        help_text='Snapshot: base-currency units per one order-currency unit.')
    status = models.ForeignKey(TradeStatus, to_field='code', on_delete=models.PROTECT,
                               default='draft', related_name='purchase_orders')
    supplier_reference = models.CharField(max_length=100, blank=True)
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
    attachment = models.FileField(upload_to='purchase_attachments/', blank=True)
    staff_note = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-order_date', '-pk']
        permissions = [
            ('receive_purchase', 'Can receive purchase orders'),
            ('duplicate_purchase', 'Can duplicate purchase orders'),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(exchange_rate__gt=0), name='purchase_order_rate_positive'),
            models.CheckConstraint(condition=Q(shipping_cost__gte=0), name='purchase_order_shipping_nonneg'),
            models.CheckConstraint(condition=Q(expected_date__isnull=True) | Q(expected_date__gte=F('order_date')),
                                   name='purchase_order_dates_valid'),
        ]

    def clean(self):
        super().clean()
        from django.conf import settings

        errors = {}
        if self.supplier_id:
            party_model = self._meta.get_field('supplier').related_model
            eligible = party_model.objects.filter(pk=self.supplier_id, is_supplier=True).exists()
            # Keep an existing historical link usable when only other order fields change.
            unchanged = not eligible and self.pk is not None and type(self).objects.filter(
                pk=self.pk, supplier_id=self.supplier_id,
            ).exists()
            if not eligible and not unchanged:
                errors['supplier'] = 'Choose a non-deleted party marked as a supplier.'
        if self.currency_id == settings.INVENTORY_BASE_CURRENCY and self.exchange_rate != Decimal('1'):
            errors['exchange_rate'] = 'The base currency exchange rate must be 1.'
        if self.payment_account_id:
            from accounting.services import payment_account_error

            error = payment_account_error(self.payment_account, self.warehouse_id)
            if error:
                errors['payment_account'] = error
        if errors:
            raise ValidationError(errors)

    @property
    def subtotal(self):
        return self.calculate_totals()[0]

    @property
    def tax_total(self):
        return self.calculate_totals()[1]

    @property
    def grand_total(self):
        return self.calculate_totals()[2]

    @property
    def original_payment_amount(self):
        if hasattr(self, 'posted_payment_amount'):
            return self.posted_payment_amount
        payments = list(self.payments.filter(purchase_return__isnull=True).values_list('is_reversal', 'amount'))
        if not payments:
            return None
        return sum((-amount if is_rev else amount for is_rev, amount in payments), Decimal('0.0000'))

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

    def __str__(self):
        return self.order_number


class PurchaseOrderItem(TimeStampedModel):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name='items')
    line_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='purchase_order_items')
    description = models.TextField(blank=True, help_text='Description snapshot for this order line.')
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='purchase_order_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6,
                                   validators=[MinValueValidator(Decimal('0.000001'))])
    unit_conversion_factor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'))
    base_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    batch = models.ForeignKey(
        'warehouse.StockBatch', on_delete=models.PROTECT, null=True, blank=True, related_name='purchase_items',
    )
    serial_numbers = models.JSONField(default=list, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(0)])
    discount_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                          validators=[MinValueValidator(0)], help_text='Discount for the entire line.')
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'),
                                   validators=[MinValueValidator(0), MaxValueValidator(100)])
    tax_method = models.CharField(max_length=16, choices=TaxMethod.choices, default=TaxMethod.EXCLUSIVE)
    received_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    allocated_order_discount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    allocated_order_tax = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    rounding_residual = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    unit_cost_base = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ['line_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['purchase_order', 'line_number'], name='purchase_order_line_unique'),
            models.CheckConstraint(condition=Q(line_number__gt=0), name='purchase_item_line_positive'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='purchase_item_quantity_positive'),
            models.CheckConstraint(condition=Q(unit_price__gte=0, discount_amount__gte=0), name='purchase_item_prices_nonneg'),
            models.CheckConstraint(condition=Q(discount_amount__lte=F('quantity') * F('unit_price')),
                                   name='purchase_item_discount_bounds'),
            models.CheckConstraint(condition=Q(tax_rate__gte=0, tax_rate__lte=100), name='purchase_item_tax_bounds'),
        ]

    def clean(self):
        super().clean()
        if self.product_id and self.unit_id:
            try:
                product = self.product
                unit = self.unit
            except (self._meta.get_field('product').related_model.DoesNotExist,
                    self._meta.get_field('unit').related_model.DoesNotExist):
                return  # Foreign-key validation reports a missing product.
            from inventory.models import check_units_convertible
            check_units_convertible(unit, product.unit)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is None or any(f in update_fields for f in ('quantity', 'unit', 'product')):
            if self.product_id and self.unit_id:
                from inventory.models import check_units_convertible
                factor = check_units_convertible(self.unit, self.product.unit)
                self.unit_conversion_factor = factor
                self.base_quantity = (Decimal(self.quantity) * factor).quantize(Decimal('0.000001'))
            elif self.base_quantity is None:
                self.base_quantity = self.quantity
        super().save(*args, **kwargs)

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
            - Decimal(self.allocated_order_discount or 0)
            + tax
            + Decimal(self.allocated_order_tax or 0)
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

    def __str__(self):
        return f'{self.purchase_order.order_number} / {self.line_number}'


class PurchaseReceipt(TimeStampedModel):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name='receipts')
    receipt_number = models.CharField(max_length=50, unique=True, default=generate_receipt_number, editable=False)
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='purchase_receipts')
    receipt_date = models.DateField(default=timezone.localdate)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='purchase_receipts',
    )
    notes = models.TextField(blank=True)
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        ordering = ['-receipt_date', '-pk']

    def __str__(self):
        return self.receipt_number


class PurchaseReceiptItem(TimeStampedModel):
    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.CASCADE, related_name='items')
    purchase_order_item = models.ForeignKey(PurchaseOrderItem, on_delete=models.PROTECT, related_name='receipt_items')
    unit = models.ForeignKey(
        'inventory.UnitOfMeasure', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=6,
                                   validators=[MinValueValidator(Decimal('0.000001'))])
    unit_conversion_factor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'))
    base_quantity = models.DecimalField(max_digits=18, decimal_places=6)
    batch = models.ForeignKey(
        'warehouse.StockBatch', on_delete=models.PROTECT, null=True, blank=True, related_name='receipt_items',
    )
    batch_number = models.CharField(max_length=100, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    serial_numbers = models.JSONField(default=list, blank=True)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=4)
    unit_cost_base = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        ordering = ['purchase_order_item__line_number', 'pk']

    def __str__(self):
        return f'{self.receipt.receipt_number} / {self.purchase_order_item.line_number}'


class PurchaseReturn(TimeStampedModel):
    return_number = models.CharField(max_length=50, unique=True, default=generate_return_number, editable=False)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name='returns')
    return_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    is_completed = models.BooleanField(default=False, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-return_date', '-pk']
        constraints = [models.CheckConstraint(
            condition=Q(is_completed=False, completed_at__isnull=True) | Q(is_completed=True, completed_at__isnull=False),
            name='purchase_return_completion_valid',
        )]

    def clean(self):
        super().clean()
        order = PurchaseOrder.objects.filter(pk=self.purchase_order_id).first()
        if order is None:
            return
        if not (order.is_received or order.receipt_state == PurchaseOrder.ReceiptState.PARTIAL) or order.warehouse_id is None:
            raise ValidationError({'purchase_order': 'Choose a received purchase with a receiving warehouse.'})
        if self.return_date and self.return_date < order.order_date:
            raise ValidationError({'return_date': 'The return date cannot be before the purchase order date.'})

    def __str__(self):
        return self.return_number


class PurchaseReturnItem(TimeStampedModel):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.PROTECT, related_name='items')
    purchase_order_item = models.ForeignKey(PurchaseOrderItem, on_delete=models.PROTECT, related_name='return_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6,
                                   validators=[MinValueValidator(Decimal('0.000001'))])

    class Meta:
        ordering = ['purchase_order_item__line_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['purchase_return', 'purchase_order_item'], name='purchase_return_order_item_unique'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='purchase_return_item_quantity_positive'),
        ]

    def clean(self):
        super().clean()
        if self.purchase_return_id and self.purchase_order_item_id:
            item = PurchaseOrderItem.objects.filter(pk=self.purchase_order_item_id).first()
            record = PurchaseReturn.objects.filter(pk=self.purchase_return_id).first()
            if item and record and item.purchase_order_id != record.purchase_order_id:
                raise ValidationError({'purchase_order_item': 'Choose an item from the original purchase order.'})
