import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from common.models import TimeStampedModel


def generate_transfer_number():
    return f'WT-{uuid.uuid4().hex.upper()}'

class WareHouse(TimeStampedModel):
    name = models.CharField(max_length=200)
    address = models.TextField(max_length=200)
    phone = models.CharField(max_length=200)
    email = models.EmailField(max_length=200, null=True, blank=True)
    code = models.CharField(max_length=200, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.name} {self.code or ""}'.strip()


class Stock(TimeStampedModel):
    warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='stocks')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='stocks')
    quantity = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'),
                                   validators=[MinValueValidator(Decimal('0'))])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['warehouse', 'product'], name='warehouse_stock_product_unique'),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name='warehouse_stock_quantity_nonnegative'),
        ]

    def __str__(self):
        return f'{self.product} - {self.warehouse}: {self.quantity}'


def generate_adjustment_number():
    return f'SA-{uuid.uuid4().hex.upper()}'


class StockAdjustment(TimeStampedModel):
    adjustment_number = models.CharField(max_length=50, unique=True, default=generate_adjustment_number, editable=False)
    warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='stock_adjustments')
    adjustment_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    is_completed = models.BooleanField(default=False, editable=False)
    completed_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        ordering = ['-adjustment_date', '-pk']

    def __str__(self):
        return self.adjustment_number


class StockAdjustmentItem(TimeStampedModel):
    class Operation(models.TextChoices):
        ADD = 'add', 'Add'
        SUBTRACT = 'subtract', 'Subtract'

    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name='items')
    line_number = models.PositiveIntegerField()
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='stock_adjustment_items')
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='stock_adjustment_items')
    operation = models.CharField(max_length=8, choices=Operation.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=6,
                                   validators=[MinValueValidator(Decimal('0.000001'))])

    class Meta:
        ordering = ['line_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['adjustment', 'line_number'], name='stock_adjustment_line_unique'),
            models.CheckConstraint(condition=models.Q(line_number__gt=0), name='stock_adjustment_line_positive'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='stock_adjustment_quantity_positive'),
            models.CheckConstraint(condition=models.Q(operation__in=['add', 'subtract']), name='stock_adjustment_operation_valid'),
        ]

    def __str__(self):
        return f'{self.adjustment.adjustment_number} / {self.line_number}'


class WarehouseTransfer(TimeStampedModel):
    """A draft containing product lines; stock moves only on completion."""

    transfer_number = models.CharField(max_length=50, unique=True, default=generate_transfer_number, editable=False)
    source_warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='outgoing_transfers')
    destination_warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='incoming_transfers')
    transfer_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    is_completed = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ['-transfer_date', '-pk']
        constraints = [
            models.CheckConstraint(condition=~models.Q(source_warehouse=models.F('destination_warehouse')),
                                   name='warehouse_transfer_different_warehouses'),
        ]

    def clean(self):
        super().clean()
        if self.source_warehouse_id is not None and self.source_warehouse_id == self.destination_warehouse_id:
            raise ValidationError({'destination_warehouse': 'Choose a different warehouse from the source.'})

    def __str__(self):
        return self.transfer_number


class WarehouseTransferItem(TimeStampedModel):
    transfer = models.ForeignKey(WarehouseTransfer, on_delete=models.PROTECT, related_name='items')
    line_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='warehouse_transfer_items')
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='warehouse_transfer_items')
    quantity = models.DecimalField(max_digits=18, decimal_places=6,
                                   validators=[MinValueValidator(Decimal('0.000001'))])

    class Meta:
        ordering = ['line_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['transfer', 'line_number'], name='warehouse_transfer_line_unique'),
            models.CheckConstraint(condition=models.Q(line_number__gt=0), name='warehouse_transfer_line_positive'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='warehouse_transfer_item_quantity_positive'),
        ]

    def clean(self):
        super().clean()
        if self.product_id and self.unit_id:
            try:
                product = self.product
            except self._meta.get_field('product').related_model.DoesNotExist:
                return
            if product.unit_id != self.unit_id:
                raise ValidationError({'unit': 'Use the product unit. Unit conversion is not supported yet.'})

    def __str__(self):
        return f'{self.transfer.transfer_number} / {self.line_number}'


class RoleWarehouseAccess(TimeStampedModel):

    role = models.OneToOneField(
        'auth.Group', on_delete=models.CASCADE, related_name='warehouse_access',
    )
    warehouse = models.ForeignKey(
        WareHouse, on_delete=models.PROTECT, null=True, blank=True,
        related_name='role_access_assignments',
    )
    all_warehouses = models.BooleanField(
        default=False,
        help_text='Allow access to all warehouses, subject to the role\'s CRUD permissions.',
    )

    class Meta:
        ordering = ['role_id']
        verbose_name = 'role warehouse access'
        verbose_name_plural = 'role warehouse access assignments'
        permissions = [('manage_warehouse_access', 'Manage warehouse assignments')]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(all_warehouses=False) | models.Q(warehouse__isnull=True),
                name='warehouse_role_access_scope_valid',
            ),
        ]

    def clean(self):
        super().clean()
        if self.all_warehouses and self.warehouse_id is not None:
            raise ValidationError({'warehouse': 'Clear the warehouse when all-warehouse access is enabled.'})

    def __str__(self):
        scope = 'All warehouses' if self.all_warehouses else str(self.warehouse) if self.warehouse_id else 'No warehouse access'
        return f'{self.role.name} — {scope}'
