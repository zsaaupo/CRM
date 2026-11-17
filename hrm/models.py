"""Employee records are independent of commercial parties and login permissions."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from common.models import TimeStampedModel, Status


class HRMLookup(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(Lower(Trim('code')), name='%(app_label)s_%(class)s_code_ci_unique')]

    def clean(self):
        super().clean()
        errors = {}
        for field in ['code', 'name']:
            value = getattr(self, field)
            if isinstance(value, str):
                value = value.strip()
                setattr(self, field, value.lower() if field == 'code' else value)
                if not value:
                    errors[field] = 'This field cannot be blank.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class Department(HRMLookup):
    class Meta(HRMLookup.Meta):
        abstract = False
        verbose_name = 'department'
        verbose_name_plural = 'departments'


class Designation(HRMLookup):
    class Meta(HRMLookup.Meta):
        abstract = False
        verbose_name = 'designation'
        verbose_name_plural = 'designations'


class EmploymentType(HRMLookup):
    class Meta(HRMLookup.Meta):
        abstract = False
        verbose_name = 'employment type'
        verbose_name_plural = 'employment types'


class EmployeeStatus(Status):
    class Meta(Status.Meta):
        abstract = False
        verbose_name = 'employee status'
        verbose_name_plural = 'employee statuses'


class EmployeeQuerySet(models.QuerySet):
    def delete(self, *, actor=None):
        """Archive records without removing employment history or changing login access."""
        now = timezone.now()
        changes = {'is_archived': True, 'archived_at': now, 'archived_by': actor, 'updated_at': now}
        if actor is not None:
            changes['modified_by'] = actor
        count = self.filter(is_archived=False).update(**changes)
        return count, {self.model._meta.label: count}


class EmployeeManager(models.Manager.from_queryset(EmployeeQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_archived=False)


class Employee(TimeStampedModel):
    employee_code = models.CharField(max_length=40, unique=True)
    full_name = models.CharField(max_length=150)
    image = models.ImageField(upload_to='employee_pictures/', blank=True)
    work_email = models.EmailField(blank=True)
    work_phone = models.CharField(max_length=32, blank=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, null=True, blank=True, related_name='employees')
    designation = models.ForeignKey(Designation, on_delete=models.PROTECT, null=True, blank=True, related_name='employees')
    employment_type = models.ForeignKey(EmploymentType, to_field='code', on_delete=models.PROTECT,
        default='full-time', related_name='employees')
    status = models.ForeignKey(EmployeeStatus, to_field='code', on_delete=models.PROTECT,
        default='active', related_name='employees')
    joining_date = models.DateField(default=timezone.localdate)
    leaving_date = models.DateField(null=True, blank=True)
    reports_to = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='direct_reports')
    workplace = models.CharField(max_length=200, blank=True, help_text='Office, site or other work location.')
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, null=True, blank=True,
        related_name='employees', help_text='Worksite reference only; this does not grant warehouse access.')
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name='employee', help_text='Optional login link, managed separately from employee details and role assignment.')
    is_archived = models.BooleanField(default=False, editable=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    archived_by = models.CharField(max_length=255, null=True, blank=True, editable=False)

    objects = EmployeeManager()
    all_objects = EmployeeQuerySet.as_manager()

    class Meta:
        ordering = ['full_name', 'pk']
        default_manager_name = 'objects'
        base_manager_name = 'all_objects'
        constraints = [
            models.UniqueConstraint(Lower(Trim('employee_code')), name='hrm_employee_code_ci_unique'),
            models.CheckConstraint(condition=Q(leaving_date__isnull=True) | Q(leaving_date__gte=F('joining_date')),
                                   name='hrm_employee_dates_valid'),
            models.CheckConstraint(condition=Q(reports_to__isnull=True) | ~Q(reports_to=F('pk')),
                                   name='hrm_employee_not_own_manager'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for field in ['employee_code', 'full_name']:
            value = getattr(self, field)
            if isinstance(value, str):
                value = value.strip()
                setattr(self, field, value.upper() if field == 'employee_code' else value)
                if not value:
                    errors[field] = 'This field cannot be blank.'
        if isinstance(self.work_email, str):
            self.work_email = self.work_email.strip().lower()
        if self.joining_date and self.leaving_date and self.leaving_date < self.joining_date:
            errors['leaving_date'] = 'The leaving date cannot be before the joining date.'
        # Include archived managers when validating the graph so history remains usable.
        manager_id = self.reports_to_id
        seen = {self.pk} if self.pk is not None else set()
        while manager_id is not None:
            if manager_id in seen:
                errors['reports_to'] = 'An employee cannot report to themselves or create a reporting cycle.'
                break
            seen.add(manager_id)
            manager_id = type(self).all_objects.filter(pk=manager_id).values_list('reports_to_id', flat=True).first()
        if errors:
            raise ValidationError(errors)

    def validate_unique(self, exclude=None):
        super().validate_unique(exclude=exclude)
        errors = {}
        if 'employee_code' not in (exclude or ()) and self.employee_code:
            duplicates = type(self).all_objects.annotate(normalized_code=Lower(Trim('employee_code'))).filter(
                normalized_code=self.employee_code.strip().lower())
            if self.pk is not None:
                duplicates = duplicates.exclude(pk=self.pk)
            if duplicates.exists():
                errors['employee_code'] = 'An employee with this code already exists, including archived employees.'
        if 'user' not in (exclude or ()) and self.user_id:
            linked = type(self).all_objects.filter(user_id=self.user_id)
            if self.pk is not None:
                linked = linked.exclude(pk=self.pk)
            if linked.exists():
                errors['user'] = 'This login is already linked to an employee, including archived employees.'
        if errors:
            raise ValidationError(errors)

    def delete(self, using=None, keep_parents=False, *, actor=None):
        if self.pk is None:
            raise ValueError('An unsaved employee cannot be archived.')
        result = type(self).all_objects.db_manager(using or self._state.db).filter(pk=self.pk).delete(actor=actor)
        if result[0]:
            self.refresh_from_db(using=using, fields=['is_archived', 'archived_at', 'archived_by', 'updated_at', 'modified_by'])
        return result

    def __str__(self):
        return f'{self.employee_code} - {self.full_name}'
