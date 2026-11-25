from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from common.models import AppendOnlyManager, TimeStampedModel


class CustomerGroup(TimeStampedModel):
    class PriceBasis(models.TextChoices):
        RETAIL = 'retail', 'Retail'
        WHOLESALE = 'wholesale', 'Wholesale'

    name = models.CharField(max_length=120, unique=True)
    percentage = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0'))
    price_basis = models.CharField(max_length=16, choices=PriceBasis.choices, default=PriceBasis.RETAIL)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'pk']
        permissions = [('manage_customer_group', 'Can manage customer groups')]

    def clean(self):
        super().clean()
        if isinstance(self.name, str):
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({'name': 'Enter a customer group name.'})

    def __str__(self):
        return self.name


class BillerQuerySet(models.QuerySet):
    def delete(self, *, actor=None):
        now = timezone.now()
        changes = {'is_deleted': True, 'deleted_at': now, 'deleted_by': actor, 'updated_at': now}
        if actor is not None:
            changes['modified_by'] = actor
        count = self.filter(is_deleted=False).update(**changes)
        return count, {self.model._meta.label: count}


class BillerManager(models.Manager.from_queryset(BillerQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class Biller(TimeStampedModel):
    name = models.CharField(max_length=200)
    company_name = models.CharField(max_length=200, blank=True)
    image = models.ImageField(upload_to='billers/', blank=True)
    vat_number = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='biller_profiles',
    )
    is_deleted = models.BooleanField(default=False, editable=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_by = models.CharField(max_length=255, null=True, blank=True, editable=False)

    objects = BillerManager()
    all_objects = BillerQuerySet.as_manager()

    class Meta:
        ordering = ['name', 'pk']
        permissions = [('manage_biller', 'Can manage billers')]

    def delete(self, using=None, keep_parents=False, *, actor=None):
        if self.pk is None:
            raise ValueError('An unsaved biller cannot be deleted.')
        queryset = type(self).all_objects.db_manager(using or self._state.db).filter(pk=self.pk)
        result = queryset.delete(actor=actor)
        if result[0]:
            self.refresh_from_db(using=using, fields=['is_deleted', 'deleted_at', 'deleted_by',
                                                     'updated_at', 'modified_by'])
        return result

    def __str__(self):
        return self.name


class BillerWarehouse(TimeStampedModel):
    biller = models.ForeignKey(Biller, on_delete=models.CASCADE, related_name='warehouse_links')
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, related_name='biller_links')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['biller', 'warehouse'], name='people_biller_warehouse_unique'),
        ]

    def __str__(self):
        return f'{self.biller} @ {self.warehouse}'


class PartyQuerySet(models.QuerySet):
    def delete(self, *, actor=None):
        """Mark matching parties as deleted without removing rows or related records."""
        now = timezone.now()
        changes = {'is_deleted': True, 'deleted_at': now, 'deleted_by': actor, 'updated_at': now}
        if actor is not None:
            changes['modified_by'] = actor
        count = self.filter(is_deleted=False).update(**changes)
        return count, {self.model._meta.label: count}


class PartyManager(models.Manager.from_queryset(PartyQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class Lead(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = 'new', 'New'
        CONTACTED = 'contacted', 'Contacted'
        QUALIFIED = 'qualified', 'Qualified'
        PROPOSAL = 'proposal', 'Proposal'
        CONVERTED = 'converted', 'Converted'
        LOST = 'lost', 'Lost'

    name = models.CharField(max_length=200)
    company = models.CharField(max_length=200, blank=True)
    job_title = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    source = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    expected_value = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='assigned_leads', null=True, blank=True,
    )
    follow_up_date = models.DateField(null=True, blank=True)
    expected_close_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class Party(TimeStampedModel):
    """One contact can be a customer, a supplier, or both."""

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    image = models.ImageField(upload_to='people_picture/', blank=True)
    is_customer = models.BooleanField(default=False)
    is_supplier = models.BooleanField(default=False)
    contact_person = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    alternate_phone = models.CharField(max_length=32, blank=True)
    website = models.URLField(blank=True)
    tax_number = models.CharField(max_length=64, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    customer_group = models.ForeignKey(
        CustomerGroup, on_delete=models.PROTECT, null=True, blank=True, related_name='parties',
    )
    discount_plan = models.ForeignKey(
        'sale.DiscountPlan', on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_parties',
    )
    deposit_balance = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal('0'))
    reward_points = models.IntegerField(default=0)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='party_profile',
    )
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False, editable=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_by = models.CharField(max_length=255, null=True, blank=True, editable=False)

    objects = PartyManager()
    all_objects = PartyQuerySet.as_manager()

    class Meta:
        ordering = ['name', 'pk']
        verbose_name_plural = 'parties'
        default_manager_name = 'objects'
        base_manager_name = 'all_objects'
        permissions = [('create_customer_login', 'Can create login user for customer party')]
        constraints = [
            models.UniqueConstraint(Lower(Trim('code')), name='people_party_code_ci_unique'),
            models.CheckConstraint(condition=Q(is_customer=True) | Q(is_supplier=True),
                                   name='people_party_requires_role'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for field in ('code', 'name'):
            value = getattr(self, field)
            if isinstance(value, str):
                value = value.strip()
                setattr(self, field, value.upper() if field == 'code' else value)
                if not value:
                    errors[field] = 'This field cannot be blank.'
        if not self.is_customer and not self.is_supplier:
            errors['__all__'] = 'A party must be a customer, a supplier, or both.'
        if errors:
            raise ValidationError(errors)

    def validate_unique(self, exclude=None):
        super().validate_unique(exclude=exclude)
        if 'code' not in (exclude or ()) and self.code:
            duplicates = type(self).all_objects.filter(code__iexact=self.code)
            if self.pk is not None:
                duplicates = duplicates.exclude(pk=self.pk)
            if duplicates.exists():
                raise ValidationError({'code': 'A party with this code already exists, including deleted parties.'})

    def delete(self, using=None, keep_parents=False, *, actor=None):
        if self.pk is None:
            raise ValueError('An unsaved party cannot be deleted.')
        queryset = type(self).all_objects.db_manager(using or self._state.db).filter(pk=self.pk)
        result = queryset.delete(actor=actor)
        if result[0]:
            self.refresh_from_db(using=using, fields=['is_deleted', 'deleted_at', 'deleted_by',
                                                     'updated_at', 'modified_by'])
        return result

    def __str__(self):
        return f'{self.code} — {self.name}'


class CustomerPointLedger(TimeStampedModel):
    class Reason(models.TextChoices):
        EARN = 'earn', 'Earn'
        REDEEM = 'redeem', 'Redeem'
        REVERSE = 'reverse', 'Reverse'
        EXPIRE = 'expire', 'Expire'

    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='point_entries')
    sales_order = models.ForeignKey(
        'sale.SalesOrder', on_delete=models.PROTECT, null=True, blank=True, related_name='point_entries',
    )
    points_delta = models.IntegerField()
    reason = models.CharField(max_length=16, choices=Reason.choices)
    expires_at = models.DateField(null=True, blank=True)
    rule_snapshot = models.JSONField(default=dict, blank=True)
    request_key = models.CharField(max_length=128, unique=True)

    objects = AppendOnlyManager()

    class Meta:
        ordering = ['pk']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('CustomerPointLedger is append-only.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('CustomerPointLedger is append-only.')

    def __str__(self):
        return f'{self.customer_id}:{self.reason}:{self.points_delta}'
