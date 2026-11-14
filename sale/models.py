import uuid
from decimal import Decimal, ROUND_HALF_UP, localcontext

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from common.models import Status, TimeStampedModel


MONEY_PRECISION = Decimal('0.0001')


def generate_order_number():
    return f'SO-{uuid.uuid4().hex.upper()}'


class SalesOrder(TimeStampedModel):
    order_number = models.CharField(max_length=50, unique=True, default=generate_order_number, editable=False)
    customer = models.ForeignKey('people.Party', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='sales_orders')
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='sales_orders')
    order_date = models.DateField(default=timezone.localdate)
    expected_delivery_date = models.DateField(null=True, blank=True)
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT,
                                 related_name='sales_orders')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8,
                                        validators=[MinValueValidator(Decimal('0.00000001'))],
                                        help_text='Snapshot: base-currency units per one order-currency unit.')
    status = models.ForeignKey(Status, to_field='code', on_delete=models.PROTECT,
                               default='draft', related_name='sales_orders')
    customer_reference = models.CharField(max_length=100, blank=True)
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    shipping_cost = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'),
                                        validators=[MinValueValidator(0)])
    notes = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    fulfilled_at = models.DateTimeField(null=True, blank=True, editable=False)

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

    def calculate_totals(self):
        with localcontext() as context:
            context.prec = 50
            subtotal = tax = Decimal('0.0000')
            if self.pk:
                for item in self.items.all():
                    net, item_tax, _ = item.calculate_amounts()
                    subtotal += net
                    tax += item_tax
            return subtotal, tax, subtotal + tax + Decimal(self.shipping_cost)

    def __str__(self):
        return self.order_number


class SalesOrderItem(TimeStampedModel):
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

    def clean(self):
        super().clean()
        if self.product_id and self.unit_id:
            try:
                product = self.product
            except self._meta.get_field('product').related_model.DoesNotExist:
                return  # Foreign-key validation reports a missing product.
            if self.unit_id != product.unit_id:
                raise ValidationError({'unit': 'Use the product unit. Unit conversion is not supported yet.'})

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
                MONEY_PRECISION, rounding=ROUND_HALF_UP)
            tax = (net * Decimal(self.tax_rate) / Decimal('100')).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
            return net, tax, net + tax

    def __str__(self):
        return f'{self.sales_order.order_number} / {self.line_number}'
