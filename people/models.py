from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from common.models import TimeStampedModel


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
