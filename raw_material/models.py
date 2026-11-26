from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from common.models import TimeStampedModel
from inventory.models import RAW_MATERIAL_TYPE


class RawMaterial(TimeStampedModel):
    product = models.OneToOneField(
        'inventory.Product', on_delete=models.CASCADE, primary_key=True,
        related_name='raw_material', limit_choices_to={'product_type_id': RAW_MATERIAL_TYPE},
    )
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='raw_materials')
    reorder_level = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True,
                                        validators=[MinValueValidator(Decimal('0'))])

    class Meta:
        ordering = ['product__name', 'pk']
        constraints = [models.CheckConstraint(
            condition=Q(reorder_level__isnull=True) | Q(reorder_level__gte=0),
            name='raw_material_reorder_level_nonnegative',
        )]

    def clean(self):
        super().clean()
        if self.warehouse_id is None:
            raise ValidationError({'warehouse': 'Choose an active warehouse.'})
        warehouses = self._meta.get_field('warehouse').related_model.objects
        if (not warehouses.filter(pk=self.warehouse_id, is_active=True).exists()
                and not type(self).objects.filter(pk=self.pk, warehouse_id=self.warehouse_id).exists()):
            raise ValidationError({'warehouse': 'Choose an active warehouse.'})

    @property
    def sku(self):
        return self.product.sku

    @property
    def quantity(self):
        if hasattr(self, 'on_hand'):
            return self.on_hand
        return (self.product.stocks.filter(warehouse_id=self.warehouse_id)
                .values_list('quantity', flat=True).first() or Decimal('0'))

    @property
    def low_stock(self):
        return self.reorder_level is not None and self.quantity <= self.reorder_level

    def __str__(self):
        return self.sku
