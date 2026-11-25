import uuid
from decimal import Decimal, localcontext

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim
from common.models import ProductStatus, TaxMethod, TimeStampedModel


def check_units_convertible(unit, target_unit) -> Decimal:
    """Return conversion factor (1 unit = factor * target_unit) or raise ValidationError."""
    if unit is None or target_unit is None:
        return Decimal('1')
    if unit.pk == target_unit.pk:
        return Decimal('1')
    return unit.conversion_factor_to(target_unit)


def require_convertible_unit(unit, target_unit, *, field: str = 'items', message: str | None = None) -> Decimal:
    """DRF-safe wrapper around check_units_convertible for service-layer calls."""
    from rest_framework.exceptions import ValidationError as DRFValidationError
    try:
        return check_units_convertible(unit, target_unit)
    except ValidationError as exc:
        raise DRFValidationError({field: message or 'Choose a unit convertible to the product base unit.'}) from exc


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
    class Operator(models.TextChoices):
        MULTIPLY = '*', 'Multiply (*)'
        DIVIDE = '/', 'Divide (/)'

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True, help_text='Examples: PCS, KG, L, HOUR.')
    base_unit = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True, related_name='derived_units',
    )
    operator = models.CharField(max_length=1, choices=Operator.choices, default=Operator.MULTIPLY)
    operation_value = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1'),
        validators=[MinValueValidator(Decimal('0.000001'))],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.CheckConstraint(condition=Q(operation_value__gt=0), name='inventory_uom_op_val_pos'),
        ]

    @property
    def factor(self) -> Decimal:
        """Conversion factor relative to this unit's root base unit (1 self = factor * root_base)."""
        if not self.base_unit_id:
            return Decimal('1')
        visited = {self.pk}
        node = self
        mult = Decimal('1')
        with localcontext() as ctx:
            ctx.prec = 50
            while node.base_unit_id:
                op_val = Decimal(node.operation_value)
                if node.operator == self.Operator.DIVIDE:
                    mult = mult / op_val
                else:
                    mult = mult * op_val
                parent = node.base_unit
                if parent.pk in visited:
                    raise ValidationError({'base_unit': 'Unit hierarchy cannot contain a cycle.'})
                visited.add(parent.pk)
                node = parent
        return mult.quantize(Decimal('0.000001'))

    def root_base_unit(self):
        visited = {self.pk}
        node = self
        while node.base_unit_id:
            parent = node.base_unit
            if parent.pk in visited:
                raise ValidationError({'base_unit': 'Unit hierarchy cannot contain a cycle.'})
            visited.add(parent.pk)
            node = parent
        return node

    def conversion_factor_to(self, target_unit) -> Decimal:
        if self.pk == target_unit.pk:
            return Decimal('1')
        if self.root_base_unit().pk != target_unit.root_base_unit().pk:
            raise ValidationError({'unit': 'Choose a unit convertible to the product base unit.'})
        with localcontext() as ctx:
            ctx.prec = 50
            return (self.factor / target_unit.factor).quantize(Decimal('0.000001'))

    def clean(self):
        super().clean()
        if self.operation_value is not None and self.operation_value <= 0:
            raise ValidationError({'operation_value': 'Operation value must be positive.'})
        if self.base_unit_id:
            if self.pk and self.base_unit_id == self.pk:
                raise ValidationError({'base_unit': 'A unit cannot be its own base unit.'})
            visited = {self.pk} if self.pk else set()
            cursor = self.base_unit
            while cursor is not None:
                if cursor.pk in visited:
                    raise ValidationError({'base_unit': 'Unit hierarchy cannot contain a cycle.'})
                visited.add(cursor.pk)
                cursor = cursor.base_unit

    def __str__(self):
        return f'{self.name} ({self.code})'


class TaxRule(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    rate = models.DecimalField(
        max_digits=7, decimal_places=4, validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return f'{self.name} ({self.rate}%)'


class ProductType(TimeStampedModel):
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']

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
    class SupplyMethod(models.TextChoices):
        PURCHASED = 'purchased', 'Purchased'
        IN_HOUSE_PRODUCTION = 'in_house_production', 'In-house production'

    class BarcodeSymbology(models.TextChoices):
        CODE128 = 'code128', 'Code 128'
        CODE39 = 'code39', 'Code 39'
        EAN13 = 'ean13', 'EAN-13'
        UPCA = 'upca', 'UPC-A'

    # Identification and classification. Each sellable variant gets its own SKU.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, db_index=True)
    barcode = models.CharField(max_length=64, blank=True)
    barcode_symbology = models.CharField(
        max_length=16, choices=BarcodeSymbology.choices, default=BarcodeSymbology.CODE128,
    )
    embedded_barcode = models.BooleanField(default=False)
    product_type = models.ForeignKey(ProductType, to_field='code', on_delete=models.PROTECT, default='goods', related_name='products')
    supply_method = models.CharField(
        max_length=24, choices=SupplyMethod.choices, default=SupplyMethod.PURCHASED,
    )
    status = models.ForeignKey(ProductStatus, to_field='code', on_delete=models.PROTECT, default='draft', related_name='products')
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, null=True, blank=True, related_name='products')
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, null=True, blank=True, related_name='products')
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name='products')
    sale_unit = models.ForeignKey(
        UnitOfMeasure, on_delete=models.PROTECT, null=True, blank=True, related_name='sale_products',
    )
    purchase_unit = models.ForeignKey(
        UnitOfMeasure, on_delete=models.PROTECT, null=True, blank=True, related_name='purchase_products',
    )
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
    tax_rule = models.ForeignKey(TaxRule, on_delete=models.PROTECT, null=True, blank=True, related_name='products')
    tax_method = models.CharField(max_length=16, choices=TaxMethod.choices, default=TaxMethod.EXCLUSIVE)
    alert_quantity = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    daily_sale_objective = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    is_featured = models.BooleanField(default=False)
    has_batch_expiry = models.BooleanField(default=False)
    has_imei_serial = models.BooleanField(default=False)
    is_promo = models.BooleanField(default=False)
    promo_price = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    promo_start_date = models.DateField(null=True, blank=True)
    promo_end_date = models.DateField(null=True, blank=True)
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
        if self.promo_start_date and self.promo_end_date and self.promo_end_date < self.promo_start_date:
            errors['promo_end_date'] = 'Promo end date cannot be before promo start date.'
        if self.is_promo and self.promo_price is None:
            errors['promo_price'] = 'Specify a promotional price.'
        if self.supply_method == self.SupplyMethod.IN_HOUSE_PRODUCTION:
            if self.product_type_id != 'goods' or self.variant_of_id is not None:
                errors['supply_method'] = 'In-house production is only supported on standard non-variant products.'
        if self.unit_id:
            for ufield in ('sale_unit', 'purchase_unit'):
                uobj = getattr(self, ufield, None)
                if uobj is not None:
                    try:
                        check_units_convertible(uobj, self.unit)
                    except ValidationError:
                        errors[ufield] = 'Unit must be convertible to the product base unit.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.sku} — {self.name}'


class ProductComboItem(TimeStampedModel):
    combo_product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='combo_items')
    component_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='used_in_combos')
    quantity = models.DecimalField(
        max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))],
    )
    unit_price = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(0)],
    )

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['combo_product', 'component_product'], name='inventory_combo_component_unique'),
            models.CheckConstraint(condition=~Q(combo_product=F('component_product')), name='inventory_combo_not_self'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='inventory_combo_qty_pos'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.combo_product_id and self.component_product_id:
            if self.combo_product_id == self.component_product_id:
                errors['component_product'] = 'A combo product cannot contain itself.'
            combo = Product.objects.filter(pk=self.combo_product_id).first()
            comp = Product.objects.filter(pk=self.component_product_id).first()
            if combo and combo.product_type_id != 'combo':
                errors['combo_product'] = 'Only products of type "combo" can own combo components.'
            if comp and comp.product_type_id == 'combo':
                errors['component_product'] = 'A combo product cannot contain another combo product.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.combo_product_id} -> {self.component_product_id} x {self.quantity}'


class WarehouseProductPrice(TimeStampedModel):
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='product_prices')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='warehouse_prices')
    sale_price = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    wholesale_price = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    alert_quantity = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['warehouse', 'product', '-effective_from']
        constraints = [
            models.UniqueConstraint(fields=['warehouse', 'product', 'effective_from'], name='inv_wh_price_eff_unique'),
            models.UniqueConstraint(
                fields=['warehouse', 'product'], condition=Q(effective_from__isnull=True),
                name='inv_wh_price_default_unique',
            ),
        ]

    def clean(self):
        super().clean()
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end date cannot precede effective start date.'})

    def __str__(self):
        return f'{self.warehouse_id}:{self.product_id} @ {self.sale_price}'
