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


class StockMovement(models.Model):
    class Kind(models.TextChoices):
        OPENING = 'opening', 'Opening balance'
        RECEIPT = 'receipt', 'Purchase receipt'
        ISSUE = 'issue', 'Sale'
        SALE_RETURN = 'sale_return', 'Sales return'
        PURCHASE_RETURN = 'purchase_return', 'Purchase return'
        TRANSFER_OUT = 'transfer_out', 'Transfer out'
        TRANSFER_IN = 'transfer_in', 'Transfer in'
        COUNT = 'count', 'Physical count adjustment'

    warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT)
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT)
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    source_id = models.PositiveBigIntegerField()
    source_line_id = models.PositiveBigIntegerField(null=True)
    posting_key = models.CharField(max_length=128, unique=True)
    business_date = models.DateField(db_index=True)
    posted_at = models.DateTimeField(default=timezone.now)
    posted_by = models.CharField(max_length=255)
    unit_cost_base = models.DecimalField(max_digits=30, decimal_places=8, null=True)

    class Meta:
        ordering = ['-pk']
        indexes = [models.Index(fields=['warehouse', 'product', 'business_date'])]
        constraints = [models.CheckConstraint(condition=~models.Q(quantity=0), name='warehouse_movement_nonzero')]


class StockCount(TimeStampedModel):
    request_key = models.UUIDField(unique=True)
    warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT)
    full_count = models.BooleanField(default=True)
    note = models.CharField(max_length=250, blank=True)
    finalized_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        ordering = ['-pk']


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT)
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT)
    expected = models.DecimalField(max_digits=18, decimal_places=6)
    last_movement = models.PositiveBigIntegerField(default=0)
    counted = models.DecimalField(max_digits=18, decimal_places=6, null=True)

    class Meta:
        ordering = ['product_id']
        constraints = [models.UniqueConstraint(fields=['count', 'product'], name='warehouse_count_product')]


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
