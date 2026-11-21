from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from common.models import TimeStampedModel


class ExpenseCategory(TimeStampedModel):
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        verbose_name_plural = 'expense categories'
        constraints = [models.UniqueConstraint(Lower(Trim('name')), name='accounting_expense_category_name_unique')]

    def clean(self):
        super().clean()
        if isinstance(self.name, str):
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({'name': 'Enter a category name.'})

    def __str__(self):
        return self.name


class Expense(TimeStampedModel):
    date = models.DateField(default=timezone.localdate, db_index=True)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='expenses')
    # SQLite converts stored decimal values with 15 significant digits.
    amount = models.DecimalField(max_digits=15, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])

    class Meta:
        ordering = ['-date', '-pk']
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_expense_amount_positive')]
        indexes = [models.Index(fields=['warehouse', '-date'], name='accounting_exp_wh_date_idx')]

    def clean(self):
        super().clean()
        previous = type(self).objects.filter(pk=self.pk)
        errors = {}
        if self.category_id and not ExpenseCategory.objects.filter(pk=self.category_id, is_active=True).exists():
            if not previous.filter(category_id=self.category_id).exists():
                errors['category'] = 'Choose an active expense category.'
        if self.warehouse_id:
            warehouses = self._meta.get_field('warehouse').related_model.objects
            if not warehouses.filter(pk=self.warehouse_id, is_active=True).exists():
                if not previous.filter(warehouse_id=self.warehouse_id).exists():
                    errors['warehouse'] = 'Choose an active warehouse.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.date} - {self.category} - {self.amount}'
