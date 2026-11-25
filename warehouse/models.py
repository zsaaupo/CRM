import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from common.models import AppendOnlyManager, TimeStampedModel


def generate_transfer_number():
    return f'WT-{uuid.uuid4().hex.upper()}'


def generate_adjustment_number():
    return f'SA-{uuid.uuid4().hex.upper()}'


def generate_count_number():
    return f'SC-{uuid.uuid4().hex.upper()}'

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
    oversold_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    average_cost = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['warehouse', 'product'], name='warehouse_stock_product_unique'),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name='warehouse_stock_quantity_nonnegative'),
        ]

    def __str__(self):
        return f'{self.product} - {self.warehouse}: {self.quantity}'


class StockBatch(TimeStampedModel):
    warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='stock_batches')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='stock_batches')
    batch_number = models.CharField(max_length=100)
    expiry_date = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('0'), validators=[MinValueValidator(Decimal('0'))],
    )
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))

    class Meta:
        ordering = ['expiry_date', 'batch_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['warehouse', 'product', 'batch_number'], name='wh_batch_unique'),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name='wh_batch_qty_nonneg'),
        ]

    def __str__(self):
        return f'{self.product_id}:{self.batch_number} ({self.quantity})'


class StockSerial(TimeStampedModel):
    class Status(models.TextChoices):
        IN_STOCK = 'in_stock', 'In stock'
        SOLD = 'sold', 'Sold'
        RETURNED = 'returned', 'Returned'
        IN_TRANSIT = 'in_transit', 'In transit'
        ADJUSTED_OUT = 'adjusted_out', 'Adjusted out'

    warehouse = models.ForeignKey(
        WareHouse, on_delete=models.PROTECT, null=True, blank=True, related_name='stock_serials',
    )
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='stock_serials')
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='serials')
    serial_number = models.CharField(max_length=100)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_STOCK, db_index=True)

    class Meta:
        ordering = ['product', 'serial_number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['product', 'serial_number'], name='wh_serial_unique'),
        ]

    def __str__(self):
        return f'{self.product_id}:{self.serial_number} ({self.status})'


class StockMovement(TimeStampedModel):
    class Kind(models.TextChoices):
        OPENING = 'opening', 'Opening'
        PURCHASE_RECEIPT = 'purchase_receipt', 'Purchase receipt'
        PURCHASE_RETURN = 'purchase_return', 'Purchase return'
        SALE_ISSUE = 'sale_issue', 'Sale issue'
        SALE_RETURN = 'sale_return', 'Sale return'
        TRANSFER_OUT = 'transfer_out', 'Transfer out'
        TRANSFER_IN = 'transfer_in', 'Transfer in'
        ADJUSTMENT_ADD = 'adjustment_add', 'Adjustment add'
        ADJUSTMENT_SUB = 'adjustment_sub', 'Adjustment subtract'
        PRODUCTION_CONSUME = 'production_consume', 'Production consume'
        PRODUCTION_OUTPUT = 'production_output', 'Production output'

    warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='stock_movements')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='stock_movements')
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='movements')
    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    quantity_delta = models.DecimalField(max_digits=18, decimal_places=6)
    unit_cost_base = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    total_cost_base = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    balance_after = models.DecimalField(max_digits=18, decimal_places=6)
    movement_date = models.DateField(default=timezone.localdate, db_index=True)
    posted_at = models.DateTimeField(default=timezone.now, editable=False)
    reference_type = models.CharField(max_length=64, db_index=True)
    reference_id = models.BigIntegerField(db_index=True)
    reference_line_id = models.BigIntegerField(null=True, blank=True)
    posting_key = models.CharField(max_length=128, unique=True)
    notes = models.TextField(blank=True)

    objects = AppendOnlyManager()

    class Meta:
        ordering = ['-posted_at', '-pk']
        indexes = [
            models.Index(fields=['warehouse', 'product', 'movement_date'], name='wh_move_wh_prod_date_idx'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('StockMovement is append-only and cannot be updated.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('StockMovement is append-only and cannot be deleted.')

    def __str__(self):
        return f'{self.kind}:{self.warehouse_id}:{self.product_id}:{self.quantity_delta}'


class WarehouseTransfer(TimeStampedModel):
    """A draft containing product lines; stock moves on dispatch (out/in-transit) and receive (in)."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        COMPLETED = 'completed', 'Completed'

    transfer_number = models.CharField(max_length=50, unique=True, default=generate_transfer_number, editable=False)
    source_warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='outgoing_transfers')
    destination_warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='incoming_transfers')
    transfer_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    is_completed = models.BooleanField(default=False, editable=False)
    sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    received_at = models.DateTimeField(null=True, blank=True, editable=False)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='sent_transfers',
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='received_transfers',
    )
    shipping_cost = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal('0'), validators=[MinValueValidator(0)],
    )
    attachment = models.FileField(upload_to='transfer_attachments/', blank=True)

    class Meta:
        ordering = ['-transfer_date', '-pk']
        permissions = [
            ('dispatch_transfer', 'Can dispatch warehouse transfers'),
            ('receive_transfer', 'Can receive warehouse transfers'),
            ('import_stock', 'Can import stock movements and transfers'),
        ]
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
    unit_conversion_factor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'))
    base_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='transfer_items')
    serial_numbers = models.JSONField(default=list, blank=True)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal('0'))
    tax_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    subtotal = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    sent_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    received_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))

    @property
    def in_transit_qty(self):
        return max(Decimal(self.sent_qty or 0) - Decimal(self.received_qty or 0), Decimal('0'))

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
                unit = self.unit
            except (self._meta.get_field('product').related_model.DoesNotExist,
                    self._meta.get_field('unit').related_model.DoesNotExist):
                return
            from inventory.models import check_units_convertible
            check_units_convertible(unit, product.unit)

    def save(self, *args, **kwargs):
        if self.product_id and self.unit_id:
            from inventory.models import check_units_convertible
            factor = check_units_convertible(self.unit, self.product.unit)
            self.unit_conversion_factor = factor
            self.base_quantity = (Decimal(self.quantity) * factor).quantize(Decimal('0.000001'))
        elif self.base_quantity is None:
            self.base_quantity = self.quantity
        super().save(*args, **kwargs)

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


class StockAdjustment(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        POSTED = 'posted', 'Posted'

    reference_number = models.CharField(max_length=50, unique=True, default=generate_adjustment_number, editable=False)
    warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='stock_adjustments')
    adjustment_date = models.DateField(default=timezone.localdate)
    attachment = models.FileField(upload_to='adjustment_attachments/', blank=True)
    note = models.TextField(blank=True)
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    request_key = models.UUIDField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-adjustment_date', '-pk']
        permissions = [('post_adjustment', 'Can post stock adjustments')]

    def __str__(self):
        return f'{self.reference_number} ({self.status})'


class StockAdjustmentItem(TimeStampedModel):
    class Action(models.TextChoices):
        ADDITION = 'addition', 'Addition'
        SUBTRACTION = 'subtraction', 'Subtraction'

    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='adjustment_items')
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='adjustment_items')
    action = models.CharField(max_length=16, choices=Action.choices)
    quantity = models.DecimalField(
        max_digits=18, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))],
    )
    unit = models.ForeignKey('inventory.UnitOfMeasure', on_delete=models.PROTECT, related_name='+')
    unit_conversion_factor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1'))
    base_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='wh_adj_item_qty_pos'),
        ]

    def clean(self):
        super().clean()
        if self.product_id and self.unit_id:
            from inventory.models import check_units_convertible
            check_units_convertible(self.unit, self.product.unit)
        if self.action == self.Action.ADDITION and self.unit_cost is None:
            raise ValidationError({'unit_cost': 'Unit cost is required for stock additions.'})

    def save(self, *args, **kwargs):
        if self.product_id and self.unit_id:
            from inventory.models import check_units_convertible
            factor = check_units_convertible(self.unit, self.product.unit)
            self.unit_conversion_factor = factor
            self.base_quantity = (Decimal(self.quantity) * factor).quantize(Decimal('0.000001'))
        elif self.base_quantity is None:
            self.base_quantity = self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.adjustment.reference_number}: {self.action} {self.quantity} x {self.product_id}'


class StockCount(TimeStampedModel):
    class CountType(models.TextChoices):
        FULL = 'full', 'Full'
        PARTIAL = 'partial', 'Partial'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        COUNTED = 'counted', 'Counted'
        FINALIZED = 'finalized', 'Finalized'

    reference_number = models.CharField(max_length=50, unique=True, default=generate_count_number, editable=False)
    warehouse = models.ForeignKey(WareHouse, on_delete=models.PROTECT, related_name='stock_counts')
    count_type = models.CharField(max_length=16, choices=CountType.choices, default=CountType.FULL)
    categories = models.ManyToManyField('inventory.ProductCategory', blank=True, related_name='stock_counts')
    brands = models.ManyToManyField('inventory.Brand', blank=True, related_name='stock_counts')
    snapshot_at = models.DateTimeField(default=timezone.now)
    initial_file = models.FileField(upload_to='stock_counts/', blank=True)
    final_file = models.FileField(upload_to='stock_counts/', blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    adjustment = models.ForeignKey(
        StockAdjustment, on_delete=models.PROTECT, null=True, blank=True, related_name='stock_counts',
    )
    reconcile_intervening = models.BooleanField(default=False)

    class Meta:
        ordering = ['-snapshot_at', '-pk']
        permissions = [('manage_stock_count', 'Can manage physical stock counts')]

    def __str__(self):
        return f'{self.reference_number} ({self.status})'


class StockCountLine(TimeStampedModel):
    stock_count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='count_lines')
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='count_lines')
    expected_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    counted_qty = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    movement_since_snapshot = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    variance_qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('0'))
    needs_recount = models.BooleanField(default=False)

    class Meta:
        ordering = ['product__sku', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['stock_count', 'product', 'batch'], name='wh_count_line_batch_unique'),
            models.UniqueConstraint(
                fields=['stock_count', 'product'], condition=models.Q(batch__isnull=True),
                name='wh_count_line_nobatch_unique',
            ),
        ]

    def __str__(self):
        return f'{self.stock_count.reference_number}: {self.product_id} (exp={self.expected_qty})'
