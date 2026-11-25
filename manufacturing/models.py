import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from common.models import AppendOnlyManager, TimeStampedModel


def _mfg_ref():
    return f'MFG-{uuid.uuid4().hex[:12].upper()}'


class RawMaterial(TimeStampedModel):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    category = models.ForeignKey(
        'inventory.ProductCategory', on_delete=models.PROTECT, null=True, blank=True, related_name='raw_materials',
    )
    stock_unit = models.ForeignKey(
        'inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='raw_materials',
    )
    current_unit_cost = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('0'), validators=[MinValueValidator(0)],
    )
    reorder_point = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('0'), validators=[MinValueValidator(0)],
    )
    default_warehouse = models.ForeignKey(
        'warehouse.WareHouse', on_delete=models.PROTECT, null=True, blank=True, related_name='raw_materials',
    )
    specifications = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        permissions = [('manage_raw_material', 'Can manage raw materials')]

    def __str__(self):
        return f'{self.code} — {self.name}'


class RawMaterialStock(TimeStampedModel):
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='raw_material_stocks')
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name='stocks')
    quantity = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('0'), validators=[MinValueValidator(0)],
    )
    average_cost = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['warehouse', 'raw_material'], name='mfg_rm_stock_unique'),
            models.CheckConstraint(condition=Q(quantity__gte=0), name='mfg_rm_stock_nonneg'),
        ]

    def __str__(self):
        return f'{self.raw_material.code} @ {self.warehouse_id}: {self.quantity}'


class RawMaterialMovement(TimeStampedModel):
    class Kind(models.TextChoices):
        OPENING = 'opening', 'Opening'
        REPLENISH = 'replenish', 'Replenish'
        ADJUST_ADD = 'adjust_add', 'Adjust add'
        ADJUST_SUB = 'adjust_sub', 'Adjust subtract'
        PRODUCTION_CONSUME = 'production_consume', 'Production consume'

    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='raw_material_movements')
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name='movements')
    kind = models.CharField(max_length=24, choices=Kind.choices, db_index=True)
    quantity_delta = models.DecimalField(max_digits=18, decimal_places=6)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    balance_after = models.DecimalField(max_digits=18, decimal_places=6)
    movement_date = models.DateField(default=timezone.localdate)
    reference_type = models.CharField(max_length=64)
    reference_id = models.BigIntegerField()
    posting_key = models.CharField(max_length=128, unique=True)
    notes = models.TextField(blank=True)

    objects = AppendOnlyManager()

    class Meta:
        ordering = ['-created_at', '-pk']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('RawMaterialMovement is append-only.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('RawMaterialMovement is append-only.')


class BillOfMaterials(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        APPROVED = 'approved', 'Approved'
        ARCHIVED = 'archived', 'Archived'

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    finished_product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='boms')
    version = models.PositiveIntegerField(default=1)
    base_output_qty = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1'), validators=[MinValueValidator(Decimal('0.000001'))],
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['finished_product', '-version']
        permissions = [('approve_bom', 'Can approve bills of materials')]
        constraints = [
            models.UniqueConstraint(fields=['finished_product', 'version'], name='mfg_bom_prod_ver_unique'),
            models.CheckConstraint(condition=Q(base_output_qty__gt=0), name='mfg_bom_base_out_pos'),
        ]

    def clean(self):
        super().clean()
        if self.finished_product_id:
            p = self.finished_product
            if p.supply_method != 'in_house_production' or p.product_type_id != 'goods' or p.variant_of_id is not None:
                raise ValidationError({'finished_product': 'Finished product must be a standard non-variant product marked for in-house production.'})

    def __str__(self):
        return f'{self.code} v{self.version} ({self.status})'


class BOMComponent(TimeStampedModel):
    class WastageBasis(models.TextChoices):
        NET = 'net', 'Net'
        GROSS = 'gross', 'Gross'

    bom = models.ForeignKey(BillOfMaterials, on_delete=models.CASCADE, related_name='components')
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name='bom_usages')
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='+')
    quantity_per_base_output = models.DecimalField(
        max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))],
    )
    wastage_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0'),
        validators=[MinValueValidator(0), MaxValueValidator(Decimal('99.99'))],
    )
    wastage_basis = models.CharField(max_length=12, choices=WastageBasis.choices, default=WastageBasis.NET)
    issue_multiple = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, default=None,
        validators=[MinValueValidator(Decimal('0.000001'))],
    )

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['bom', 'raw_material'], name='mfg_bom_comp_unique'),
        ]

    def clean(self):
        super().clean()
        if self.unit_id and self.raw_material_id:
            from inventory.models import check_units_convertible
            check_units_convertible(self.unit, self.raw_material.stock_unit)


class ProductionOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        POSTED = 'posted', 'Posted'
        CANCELLED = 'cancelled', 'Cancelled'

    reference_number = models.CharField(max_length=50, unique=True, default=_mfg_ref, editable=False)
    bom = models.ForeignKey(BillOfMaterials, on_delete=models.PROTECT, related_name='production_orders')
    finished_product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='production_orders')
    material_warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='mfg_material_orders')
    finished_warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='mfg_finished_orders')
    planned_qty = models.DecimalField(
        max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))],
    )
    accepted_good_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    rejected_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    labor_cost = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'), validators=[MinValueValidator(0)])
    overhead_cost = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'), validators=[MinValueValidator(0)])
    material_cost_total = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    unit_cost_calculated = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    production_date = models.DateField(default=timezone.localdate)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    request_key = models.UUIDField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-production_date', '-pk']
        permissions = [('post_production', 'Can post production orders')]

    def __str__(self):
        return f'{self.reference_number} ({self.status})'


class ProductionConsumptionLine(TimeStampedModel):
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='consumption_lines')
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name='consumption_lines')
    planned_qty = models.DecimalField(max_digits=18, decimal_places=6)
    available_qty_snapshot = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    actual_used_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    unit_cost_snapshot = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    line_cost = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))

    class Meta:
        ordering = ['pk']
