from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from common.models import TimeStampedModel

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
