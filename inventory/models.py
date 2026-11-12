import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim
from common.models import TimeStampedModel


class ProductCategory(TimeStampedModel):
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        verbose_name_plural = 'product categories'

    def __str__(self):
        return self.name


class Brand(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    website = models.URLField(blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class UnitOfMeasure(TimeStampedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True, help_text='Examples: PCS, KG, L, HOUR.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return f'{self.name} ({self.code})'


class ProductType(TimeStampedModel):
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class Status(TimeStampedModel):
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        verbose_name = 'product status'
        verbose_name_plural = 'product statuses'

    def __str__(self):
        return self.name


class Currency(TimeStampedModel):
    code = models.CharField(max_length=3, unique=True, validators=[RegexValidator(
        r'\A[A-Z]{3}\Z', 'Use a three-letter uppercase currency code, e.g. BDT or USD.')])
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=10, blank=True)
    # NULL preserves legacy currencies whose exchange rate is not yet known.
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True,
                                        validators=[MinValueValidator(Decimal('0.00000001'))])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        verbose_name_plural = 'currencies'
        constraints = [models.CheckConstraint(
            condition=Q(exchange_rate__isnull=True) | Q(exchange_rate__gt=0),
            name='inventory_currency_rate_positive')]

    def clean(self):
        super().clean()
        from django.conf import settings
        if self.code == settings.INVENTORY_BASE_CURRENCY and self.exchange_rate != Decimal('1'):
            raise ValidationError({'exchange_rate': 'The base currency exchange rate must be 1.'})

    def __str__(self):
        return f'{self.name} ({self.code})'


class Product(TimeStampedModel):
    # Identification and classification. Each sellable variant gets its own SKU.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, db_index=True)
    barcode = models.CharField(max_length=64, blank=True)
    product_type = models.ForeignKey(ProductType, to_field='code', on_delete=models.PROTECT, default='goods', related_name='products')
    status = models.ForeignKey(Status, to_field='code', on_delete=models.PROTECT, default='draft', related_name='products')
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, null=True, blank=True, related_name='products')
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, null=True, blank=True, related_name='products')
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name='products')
    variant_of = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='variants')
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='products_image/', blank=True)
    manufacturer = models.CharField(max_length=150, blank=True)
    manufacturer_part_number = models.CharField(max_length=100, blank=True)
    attributes = models.JSONField(default=dict, blank=True, help_text='Variant/specification values, e.g. {"color": "blue", "size": "M"}.')

    # Catalog defaults. Transaction prices and tax rules belong on order/invoice lines.
    currency = models.ForeignKey(Currency, to_field='code', on_delete=models.PROTECT, related_name='products')
    cost_price = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'), validators=[MinValueValidator(0)])
    sale_price = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'), validators=[MinValueValidator(0)])
    minimum_sale_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(0)])
    Whole_sale_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(0)])
    product_tax = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'), validators=[MinValueValidator(0), MaxValueValidator(100)])
    is_returnable = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(Lower(Trim('sku')), name='inventory_product_sku_ci_unique'),
            models.UniqueConstraint(fields=['barcode'], condition=~Q(barcode=''), name='inventory_product_barcode_unique'),
            models.CheckConstraint(condition=Q(cost_price__gte=0) & Q(sale_price__gte=0) & (Q(minimum_sale_price__isnull=True) | Q(minimum_sale_price__gte=0)) & (Q(Whole_sale_price__isnull=True) | Q(Whole_sale_price__gte=0)), name='inventory_product_prices_nonneg'),
            models.CheckConstraint(condition=Q(minimum_sale_price__isnull=True) | Q(sale_price__gte=F('minimum_sale_price')), name='inventory_product_price_floor'),
            models.CheckConstraint(condition=Q(product_tax__gte=0, product_tax__lte=100), name='inventory_product_tax_bounds'),
            models.CheckConstraint(condition=Q(variant_of__isnull=True) | ~Q(variant_of=F('pk')), name='inventory_product_not_own_variant'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for field in ('sku', 'name'):
            value = getattr(self, field)
            if isinstance(value, str):
                setattr(self, field, value.strip())
                if not value.strip():
                    errors[field] = 'This field cannot be blank.'
        if not isinstance(self.attributes, dict):
            errors['attributes'] = 'Product attributes must be a JSON object.'
        if self.variant_of_id:
            try:
                parent = self.variant_of
            except Product.DoesNotExist:
                parent = None
            if self.pk and self.variant_of_id == self.pk:
                errors['variant_of'] = 'A product cannot be its own parent.'
            elif parent is None:
                errors['variant_of'] = 'Choose an existing base product.'
            elif parent.variant_of_id:
                errors['variant_of'] = 'Choose a base product, not another variant.'
            elif self.pk and self.variants.exists():
                errors['variant_of'] = 'A product with variants cannot itself become a variant.'
            elif parent.product_type_id != self.product_type_id:
                errors['variant_of'] = 'A variant must have the same product type as its parent.'
        if self.pk and self.variants.exclude(product_type_id=self.product_type_id).exists():
            errors['product_type'] = 'The product type must match its existing variants.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.sku} — {self.name}'
