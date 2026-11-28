"""Employee, notice, attendance, leave, salary configuration, and payroll models."""

from calendar import monthrange
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from common.models import TimeStampedModel, Status

from .managers import EmployeeManager, EmployeeQuerySet
from .validators import validate_working_days


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


# Employee


class Department(HRMLookup):
    class Meta(HRMLookup.Meta):
        abstract = False
        verbose_name = 'department'
        verbose_name_plural = 'departments'


class Notice(TimeStampedModel):
    title = models.CharField(max_length=255)
    message = models.TextField()
    department = models.ForeignKey(Department, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='notices', help_text='Leave empty for all employees.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False,
                                   related_name='published_notices')
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   editable=False, related_name='removed_notices')

    class Meta:
        ordering = ['-created_at', '-pk']
        default_permissions = ('view', 'add', 'delete')
        permissions = [('view_all_notice', 'Can view notices across all departments')]
        indexes = [models.Index(fields=['deleted_at', '-created_at', '-id'], name='hrm_notice_feed_idx')]
        constraints = [
            models.CheckConstraint(
                condition=Q(deleted_at__isnull=True, deleted_by__isnull=True)
                | Q(deleted_at__isnull=False, deleted_by__isnull=False),
                name='hrm_notice_removal_actor_time',
            ),
        ]


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


# Attendance


class WorkSchedule(HRMLookup):
    working_days = models.JSONField(validators=[validate_working_days],
        help_text='List of weekdays: Monday=0 through Sunday=6, e.g. [6, 0, 1, 2, 3].')
    start_time = models.TimeField()
    end_time = models.TimeField(help_text='An earlier end time means the shift ends the next day.')
    break_minutes = models.PositiveSmallIntegerField(default=0)
    grace_minutes = models.PositiveSmallIntegerField(default=0, help_text='Allowed minutes after the scheduled start.')

    class Meta(HRMLookup.Meta):
        abstract = False
        constraints = [
            *HRMLookup.Meta.constraints,
            models.CheckConstraint(condition=~Q(start_time=F('end_time')), name='hrm_schedule_times_differ'),
        ]

    def clean(self):
        super().clean()
        if self.start_time is None or self.end_time is None:
            return
        if self.start_time == self.end_time:
            raise ValidationError({'end_time': 'The shift start and end times must differ.'})
        start = self.start_time.hour * 3600 + self.start_time.minute * 60 + self.start_time.second
        end = self.end_time.hour * 3600 + self.end_time.minute * 60 + self.end_time.second
        duration_seconds = (end - start) % (24 * 3600)
        if self.break_minutes is not None and self.break_minutes * 60 >= duration_seconds:
            raise ValidationError({'break_minutes': 'The break must be shorter than the shift.'})


class EmployeeScheduleAssignment(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='schedule_assignments')
    schedule = models.ForeignKey(WorkSchedule, on_delete=models.PROTECT, related_name='employee_assignments')
    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True, help_text='Leave empty for an ongoing assignment.')

    class Meta:
        ordering = ['employee_id', '-effective_from', '-pk']
        constraints = [models.CheckConstraint(
            condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F('effective_from')),
            name='hrm_assignment_dates_valid')]

    def clean(self):
        super().clean()
        if not self.effective_from:
            return
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'The end date cannot be before the start date.'})
        if self.employee_id:
            overlapping = type(self).objects.filter(employee_id=self.employee_id).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from))
            if self.effective_to:
                overlapping = overlapping.filter(effective_from__lte=self.effective_to)
            if self.pk is not None:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError({'effective_from': 'This employee already has a schedule for part of this period.'})

    def __str__(self):
        return f'{self.employee} - {self.schedule} ({self.effective_from})'


class Holiday(TimeStampedModel):
    name = models.CharField(max_length=150)
    date = models.DateField()
    warehouse = models.ForeignKey('warehouse.WareHouse', on_delete=models.PROTECT, null=True, blank=True,
        related_name='holidays', help_text='Leave empty for a company-wide holiday; this does not grant access.')

    class Meta:
        ordering = ['date', 'name', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['date'], condition=Q(warehouse__isnull=True),
                                    name='hrm_holiday_company_date_unique'),
            models.UniqueConstraint(fields=['date', 'warehouse'], condition=Q(warehouse__isnull=False),
                                    name='hrm_holiday_warehouse_date_unique'),
        ]

    def clean(self):
        super().clean()
        if isinstance(self.name, str):
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({'name': 'This field cannot be blank.'})

    def __str__(self):
        return f'{self.name} ({self.date})'


class EmployeeAttendanceStatus(Status):
    class Code(models.TextChoices):
        UNRECORDED = 'unrecorded', 'Unrecorded'
        PRESENT = 'present', 'Present'
        ABSENT = 'absent', 'Absent'
        ON_LEAVE = 'on-leave', 'On leave'
        HOLIDAY = 'holiday', 'Holiday'
        DAY_OFF = 'day-off', 'Day off'

    class Meta(Status.Meta):
        verbose_name = 'employee attendance status'
        verbose_name_plural = 'employee attendance statuses'


class Attendance(TimeStampedModel):

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='attendance_records')
    work_date = models.DateField(default=timezone.localdate, help_text='Local date on which the shift starts.')
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    status = models.ForeignKey(EmployeeAttendanceStatus, to_field='code', on_delete=models.PROTECT,
                               default=EmployeeAttendanceStatus.Code.UNRECORDED, related_name='employees')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-work_date', 'employee_id', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'work_date'], name='hrm_attendance_employee_date_unique'),
            models.CheckConstraint(condition=Q(check_out__isnull=True) | Q(check_in__isnull=False),
                                   name='hrm_attendance_checkout_needs_in'),
            models.CheckConstraint(condition=Q(check_out__isnull=True) | Q(check_out__gte=F('check_in')),
                                   name='hrm_attendance_times_ordered'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.check_out is not None:
            if self.check_in is None:
                errors['check_out'] = 'Record a check-in before recording a check-out.'
            elif self.check_out < self.check_in:
                errors['check_out'] = 'The check-out cannot be before the check-in.'
        if self.check_in is not None and self.work_date:
            local_check_in = timezone.localtime(self.check_in) if timezone.is_aware(self.check_in) else self.check_in
            if local_check_in.date() != self.work_date:
                errors['check_in'] = 'The check-in must be on the work date in the local timezone.'
        codes = EmployeeAttendanceStatus.Code
        if self.status_id == codes.PRESENT and self.check_in is None:
            errors['check_in'] = 'A present employee must have a check-in.'
        if self.status_id in (codes.ABSENT, codes.ON_LEAVE, codes.HOLIDAY, codes.DAY_OFF):
            if self.check_in is not None or self.check_out is not None:
                errors['status'] = 'This status cannot be used with a check-in or check-out.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.employee} - {self.work_date} ({self.status_id})'


# Leave


class LeaveType(HRMLookup):
    is_paid = models.BooleanField(help_text='Whether time off under this leave type is paid.')

    class Meta(HRMLookup.Meta):
        abstract = False


class LeaveRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        CANCELLED = 'cancelled', 'Cancelled'

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='leave_requests')
    start_date = models.DateField()
    end_date = models.DateField(help_text='The end date is included in the request.')
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='reviewed_leave_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-start_date', '-pk']
        permissions = [('review_leaverequest', 'Can review leave requests')]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F('start_date')), name='hrm_leave_dates_valid'),
            models.CheckConstraint(condition=Q(status__in=['pending', 'approved', 'rejected', 'cancelled']),
                                   name='hrm_leave_status_valid'),
            models.CheckConstraint(
                condition=(Q(status__in=['pending', 'cancelled'], reviewed_by__isnull=True, reviewed_at__isnull=True)
                           | Q(status__in=['approved', 'rejected', 'cancelled'],
                               reviewed_by__isnull=False, reviewed_at__isnull=False)),
                name='hrm_leave_review_valid',
            ),
        ]
        indexes = [models.Index(fields=['employee', 'start_date', 'end_date'], condition=Q(status='approved'),
                                name='hrm_leave_approved_dates_idx')]

    def clean(self):
        super().clean()
        errors = {}
        if self.status == self.Status.PENDING and (self.reviewed_by_id is not None or self.reviewed_at is not None):
            errors['status'] = 'A pending request cannot have a review.'
        needs_review = (self.status in (self.Status.APPROVED, self.Status.REJECTED)
                        or self.reviewed_by_id is not None or self.reviewed_at is not None)
        if needs_review:
            if self.reviewed_by_id is None:
                errors['reviewed_by'] = 'Choose who reviewed the request.'
            if self.reviewed_at is None:
                errors['reviewed_at'] = 'Enter when the request was reviewed.'

        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                errors['end_date'] = 'The end date cannot be before the start date.'
            elif self.employee_id and self.status == self.Status.APPROVED:
                overlapping = type(self).objects.filter(
                    employee_id=self.employee_id, status=self.Status.APPROVED,
                    start_date__lte=self.end_date, end_date__gte=self.start_date,
                ).exclude(pk=self.pk)
                if overlapping.exists():
                    errors['start_date'] = 'This period overlaps an approved leave request for this employee.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.employee} - {self.leave_type} ({self.start_date} to {self.end_date})'


# Salary


class SalaryComponent(HRMLookup):
    """A reusable earning or deduction, independent of its calculation rule."""

    class Kind(models.TextChoices):
        EARNING = 'earning', 'Earning'
        DEDUCTION = 'deduction', 'Deduction'

    type = models.CharField(max_length=16, choices=Kind.choices)
    category = models.CharField(max_length=64, blank=True, help_text='Optional grouping, such as allowance or tax.')

    class Meta(HRMLookup.Meta):
        abstract = False
        constraints = [
            *HRMLookup.Meta.constraints,
            models.CheckConstraint(condition=Q(type__in=['earning', 'deduction']),
                                   name='hrm_salary_component_type_valid'),
        ]

    def clean(self):
        super().clean()
        if self.pk and self.type == self.Kind.DEDUCTION and self.structure_lines.filter(
            calculation_method=SalaryStructureLine.CalculationMethod.BASIC_SALARY,
        ).exists():
            raise ValidationError({'type': 'A component used as basic salary must remain an earning.'})
        if self.pk and self.type == self.Kind.EARNING and self.deduction_rules.exists():
            raise ValidationError({'type': 'A component used by a payroll deduction rule must remain a deduction.'})


class SalaryStructure(HRMLookup):
    """A compensation package shared by employees with individual basic salaries."""

    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT,
                                 related_name='salary_structures')

    class Meta(HRMLookup.Meta):
        abstract = False


class SalaryStructureLine(TimeStampedModel):
    """The calculation rule for one component in a salary structure."""

    class CalculationMethod(models.TextChoices):
        BASIC_SALARY = 'basic_salary', 'Assigned basic salary'
        FIXED = 'fixed', 'Fixed amount'
        PERCENTAGE_OF_BASIC = 'percentage_of_basic', 'Percentage of basic salary'

    salary_structure = models.ForeignKey(SalaryStructure, on_delete=models.PROTECT, related_name='lines')
    component = models.ForeignKey(SalaryComponent, on_delete=models.PROTECT, related_name='structure_lines')
    calculation_method = models.CharField(max_length=24, choices=CalculationMethod.choices)
    fixed_amount = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True,
                                       validators=[MinValueValidator(0)], help_text='In the structure currency.')
    percentage = models.DecimalField(max_digits=9, decimal_places=4, null=True, blank=True,
                                     validators=[MinValueValidator(0)], help_text='Enter 40 for 40% of basic salary.')

    class Meta:
        ordering = ['salary_structure_id', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['salary_structure', 'component'], name='hrm_salary_structure_component_unique'),
            models.UniqueConstraint(fields=['salary_structure'], condition=Q(calculation_method='basic_salary'),
                                    name='hrm_salary_structure_basic_unique'),
            models.CheckConstraint(
                condition=(Q(calculation_method='basic_salary', fixed_amount__isnull=True, percentage__isnull=True)
                           | Q(calculation_method='fixed', fixed_amount__isnull=False, fixed_amount__gte=0,
                               percentage__isnull=True)
                           | Q(calculation_method='percentage_of_basic', fixed_amount__isnull=True,
                               percentage__isnull=False, percentage__gte=0)),
                name='hrm_salary_structure_line_rule_valid',
                violation_error_message='Provide only the amount or percentage required by the calculation method.',
            ),
        ]

    def clean(self):
        super().clean()
        if self.calculation_method == self.CalculationMethod.BASIC_SALARY and SalaryComponent.objects.filter(
            pk=self.component_id, type=SalaryComponent.Kind.DEDUCTION,
        ).exists():
            raise ValidationError({'component': 'Basic salary must use an earning component.'})

    def __str__(self):
        return f'{self.salary_structure} - {self.component}'


class EmployeeCompensation(TimeStampedModel):
    """An employee's basic salary and salary structure for an inclusive date range."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='compensation_assignments')
    salary_structure = models.ForeignKey(SalaryStructure, on_delete=models.PROTECT, related_name='employee_assignments')
    basic_salary = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))],
                                       help_text='Monthly basic salary in the structure currency.')
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True, help_text='Last applicable day. Leave empty for ongoing pay.')

    class Meta:
        ordering = ['employee_id', '-effective_from', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'effective_from'], name='hrm_compensation_employee_start_unique'),
            models.UniqueConstraint(fields=['employee'], condition=Q(effective_to__isnull=True),
                                    name='hrm_compensation_employee_open_unique'),
            models.CheckConstraint(condition=Q(basic_salary__gt=0), name='hrm_compensation_basic_positive'),
            models.CheckConstraint(condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F('effective_from')),
                                   name='hrm_compensation_dates_valid'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.salary_structure_id and not SalaryStructureLine.objects.filter(
            salary_structure_id=self.salary_structure_id,
            calculation_method=SalaryStructureLine.CalculationMethod.BASIC_SALARY,
        ).exists():
            errors['salary_structure'] = 'Add a basic salary line to the structure before assigning it.'
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            errors['effective_to'] = 'The end date cannot be before the start date.'
        elif self.employee_id and self.effective_from:
            overlapping = type(self).objects.filter(employee_id=self.employee_id).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from))
            if self.effective_to:
                overlapping = overlapping.filter(effective_from__lte=self.effective_to)
            if overlapping.exclude(pk=self.pk).exists():
                errors['effective_from'] = 'This employee already has compensation for part of this period.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.employee} - {self.salary_structure} ({self.effective_from})'


# Payroll


class PayrollPeriod(TimeStampedModel):
    month = models.DateField(unique=True, help_text='First calendar day of the payroll month.')

    class Meta:
        ordering = ['-month']
        constraints = [models.CheckConstraint(condition=Q(month__day=1), name='hrm_period_month_starts_on_first')]

    @property
    def end_date(self):
        return self.month.replace(day=monthrange(self.month.year, self.month.month)[1])

    def __str__(self):
        return self.month.strftime('%Y-%m')


class PayrollRun(TimeStampedModel):
    class Status(models.TextChoices):
        CALCULATED = 'calculated', 'Calculated'
        REVIEWED = 'reviewed', 'Reviewed'
        REJECTED = 'rejected', 'Rejected'
        CANCELLED = 'cancelled', 'Cancelled'
        APPROVED = 'approved', 'Approved'
        PAYMENT_PROCESSING = 'payment_processing', 'Payment processing'
        PAID = 'paid', 'Paid'

    period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name='runs')
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT, related_name='payroll_runs')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CALCULATED, editable=False)
    calculated_at = models.DateTimeField(default=timezone.now, editable=False)
    calculated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='calculated_payroll_runs')
    input_digest = models.CharField(max_length=64, editable=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    editable=False, related_name='reviewed_payroll_runs')
    reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    editable=False, related_name='approved_payroll_runs')
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-period__month', 'currency_id', 'pk']
        permissions = [
            ('calculate_payrollrun', 'Can preview and calculate payroll runs'),
            ('review_payrollrun', 'Can review, reject and cancel payroll runs'),
            ('approve_payrollrun', 'Can approve payroll runs'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['period', 'currency'], name='hrm_payroll_period_currency_unique'),
            models.CheckConstraint(condition=Q(status__in=['calculated', 'reviewed', 'rejected', 'cancelled', 'approved',
                                                           'payment_processing', 'paid']), name='hrm_payroll_status_valid'),
        ]

    def __str__(self):
        return f'{self.period} ({self.currency_id})'


class Payslip(TimeStampedModel):
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='payslips')
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='payslips')
    employee_code = models.CharField(max_length=40, editable=False)
    employee_name = models.CharField(max_length=150, editable=False)
    department_name = models.CharField(max_length=150, blank=True, editable=False)
    gross_pay = models.DecimalField(max_digits=18, decimal_places=4)
    total_deductions = models.DecimalField(max_digits=18, decimal_places=4)
    net_pay = models.DecimalField(max_digits=18, decimal_places=4)
    calculation_inputs = models.JSONField(default=dict, editable=False)
    warnings = models.JSONField(default=list, editable=False)

    class Meta:
        ordering = ['employee_name', 'employee_id']
        constraints = [
            models.UniqueConstraint(fields=['payroll_run', 'employee'], name='hrm_payslip_run_employee_unique'),
            models.CheckConstraint(condition=Q(gross_pay__gte=0, total_deductions__gte=0, net_pay__gte=0,
                                               total_deductions__lte=F('gross_pay')),
                                   name='hrm_payslip_amounts_valid'),
        ]

    def __str__(self):
        return f'{self.employee_code} - {self.payroll_run}'


class PayslipLine(TimeStampedModel):
    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='lines')
    code = models.CharField(max_length=40, editable=False)
    name = models.CharField(max_length=150, editable=False)
    type = models.CharField(max_length=16, choices=SalaryComponent.Kind.choices, editable=False)
    amount = models.DecimalField(max_digits=18, decimal_places=4)
    calculation_inputs = models.JSONField(default=list, editable=False)

    class Meta:
        ordering = ['pk']
        constraints = [models.CheckConstraint(condition=Q(amount__gte=0, type__in=['earning', 'deduction']),
                                              name='hrm_payslip_line_valid')]

    def __str__(self):
        return self.name


class PayrollEvent(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='payroll_events')
    action = models.CharField(max_length=32)
    note = models.TextField(blank=True)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['created_at', 'pk']


class SalaryPayment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'

    class Method(models.TextChoices):
        BANK = 'bank', 'Bank transfer'
        CASH = 'cash', 'Cash'
        OTHER = 'other', 'Other'

    payslip = models.ForeignKey(Payslip, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    method = models.CharField(max_length=10, choices=Method.choices)
    request_key = models.UUIDField(unique=True, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, editable=False)
    reference = models.CharField(max_length=120, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    editable=False, related_name='resolved_salary_payments')

    class Meta:
        ordering = ['created_at', 'pk']
        permissions = [('record_salarypayment', 'Can record and resolve salary payments')]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name='hrm_salary_payment_positive'),
            models.CheckConstraint(
                condition=(Q(status='pending', resolved_at__isnull=True, resolved_by__isnull=True, paid_on__isnull=True)
                           | Q(status='failed', resolved_at__isnull=False, resolved_by__isnull=False, paid_on__isnull=True)
                           & ~Q(failure_reason='')
                           | Q(status='succeeded', resolved_at__isnull=False, resolved_by__isnull=False, paid_on__isnull=False)
                           & ~Q(reference='')),
                name='hrm_salary_payment_resolution_valid',
            ),
            models.UniqueConstraint(fields=['payslip', 'reference'], condition=Q(status='succeeded'),
                                    name='hrm_salary_payment_success_reference_unique'),
        ]


class PayrollAdjustment(TimeStampedModel):
    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual adjustment'
        BONUS = 'bonus', 'Bonus'
        OVERTIME = 'overtime', 'Overtime'
        STATUTORY = 'statutory', 'Statutory adjustment'
        CORRECTION = 'correction', 'Correction'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        APPROVED = 'approved', 'Approved'
        CANCELLED = 'cancelled', 'Cancelled'

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='payroll_adjustments')
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT, related_name='payroll_adjustments')
    component = models.ForeignKey(SalaryComponent, on_delete=models.PROTECT, related_name='payroll_adjustments')
    effective_on = models.DateField()
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    amount = models.DecimalField(max_digits=18, decimal_places=4)
    reason = models.CharField(max_length=255)
    overtime_minutes = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    hourly_rate = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True,
                                      validators=[MinValueValidator(Decimal('0.0001'))])
    multiplier = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                                      validators=[MinValueValidator(Decimal('0.0001'))])
    correction_of = models.ForeignKey(Payslip, on_delete=models.PROTECT, null=True, blank=True, related_name='corrections')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, editable=False)
    approved_type = models.CharField(max_length=16, choices=SalaryComponent.Kind.choices, blank=True, editable=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, editable=False)
    reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-effective_on', '-pk']
        permissions = [('review_payrolladjustment', 'Can approve or cancel payroll adjustments')]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name='hrm_adjustment_amount_positive'),
            models.UniqueConstraint(fields=['employee', 'effective_on'],
                condition=Q(source='overtime', status__in=['draft', 'approved']), name='hrm_overtime_employee_day_unique'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        component_type = SalaryComponent.objects.filter(pk=self.component_id).values_list('type', flat=True).first()
        if self.source in (self.Source.BONUS, self.Source.OVERTIME) and component_type != SalaryComponent.Kind.EARNING:
            errors['component'] = 'Bonuses and overtime require an earning component.'
        if self.source == self.Source.STATUTORY and component_type != SalaryComponent.Kind.DEDUCTION:
            errors['component'] = 'A statutory adjustment requires a deduction component.'
        overtime = (self.overtime_minutes, self.hourly_rate, self.multiplier)
        if self.source == self.Source.OVERTIME and not all(overtime):
            errors['overtime_minutes'] = 'Enter minutes, hourly rate, and multiplier for overtime.'
        if self.source != self.Source.OVERTIME and any(value is not None for value in overtime):
            errors['overtime_minutes'] = 'Overtime inputs are available for overtime adjustments.'
        if self.source == self.Source.CORRECTION:
            if not self.correction_of_id:
                errors['correction_of'] = 'Choose the approved payslip being corrected.'
            else:
                original = Payslip.objects.select_related('payroll_run__period').filter(pk=self.correction_of_id).first()
                if original and (original.employee_id != self.employee_id or original.payroll_run.currency_id != self.currency_id
                    or original.payroll_run.status not in ('approved', 'payment_processing', 'paid')
                    or (self.effective_on and self.effective_on <= original.payroll_run.period.end_date)):
                    errors['correction_of'] = 'Use an approved payslip for this employee and currency from an earlier period.'
        elif self.correction_of_id:
            errors['correction_of'] = 'A payslip reference is available for corrections.'
        if self.source != self.Source.CORRECTION and self.employee_id and self.effective_on:
            employee = Employee.all_objects.filter(pk=self.employee_id).first()
            if employee and (self.effective_on < employee.joining_date or
                             (employee.leaving_date and self.effective_on > employee.leaving_date)):
                errors['effective_on'] = 'Choose a date within the employee’s employment.'
        if errors:
            raise ValidationError(errors)


class PayrollDeductionRule(TimeStampedModel):
    class Method(models.TextChoices):
        FIXED = 'fixed', 'Fixed amount'
        PERCENTAGE = 'percentage_of_gross', 'Percentage of recurring gross earnings'

    salary_structure = models.ForeignKey(SalaryStructure, on_delete=models.PROTECT, related_name='deduction_rules')
    component = models.ForeignKey(SalaryComponent, on_delete=models.PROTECT, related_name='deduction_rules')
    method = models.CharField(max_length=24, choices=Method.choices)
    value = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    effective_from = models.DateField(help_text='First day of the starting payroll month.')
    effective_to = models.DateField(null=True, blank=True, help_text='First day of the last applicable payroll month.')
    reference = models.CharField(max_length=255, help_text='Reference for the payroll policy or verified statutory rule.')

    class Meta:
        ordering = ['-effective_from', '-pk']
        constraints = [
            models.CheckConstraint(condition=Q(value__gt=0) & (Q(method='fixed') | Q(method='percentage_of_gross', value__lte=100)),
                                   name='hrm_deduction_rule_amount_valid'),
            models.CheckConstraint(condition=Q(effective_from__day=1) & (Q(effective_to__isnull=True) |
                Q(effective_to__day=1, effective_to__gte=F('effective_from'))), name='hrm_deduction_rule_months_valid'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.component_id:
            if SalaryComponent.objects.filter(pk=self.component_id, type=SalaryComponent.Kind.EARNING).exists():
                errors['component'] = 'Choose a deduction component.'
            elif SalaryStructureLine.objects.filter(salary_structure_id=self.salary_structure_id, component_id=self.component_id).exists():
                errors['component'] = 'This component already has a salary structure line.'
        if self.effective_from and self.salary_structure_id and self.component_id:
            rules = type(self).objects.filter(salary_structure_id=self.salary_structure_id, component_id=self.component_id).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from)).exclude(pk=self.pk)
            if self.effective_to:
                rules = rules.filter(effective_from__lte=self.effective_to)
            if rules.exists():
                errors['effective_from'] = 'Another rule for this component covers part of that period.'
        if errors:
            raise ValidationError(errors)


class EmployeeLoan(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='payroll_loans')
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT, related_name='employee_loans')
    reference = models.CharField(max_length=80, unique=True)
    principal = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    disbursed_on = models.DateField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-disbursed_on', '-pk']
        constraints = [models.CheckConstraint(condition=Q(principal__gt=0), name='hrm_employee_loan_principal_positive')]


class LoanInstallment(TimeStampedModel):
    loan = models.ForeignKey(EmployeeLoan, on_delete=models.PROTECT, related_name='installments')
    month = models.DateField(help_text='First day of the payroll month in which this installment is due.')
    amount = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    recovered_line = models.OneToOneField(PayslipLine, on_delete=models.PROTECT, null=True, blank=True, editable=False)
    recovered_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['month', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['loan', 'month'], name='hrm_loan_installment_month_unique'),
            models.CheckConstraint(condition=Q(month__day=1, amount__gt=0), name='hrm_loan_installment_amount_date_valid'),
            models.CheckConstraint(condition=Q(recovered_line__isnull=True, recovered_at__isnull=True) |
                Q(recovered_line__isnull=False, recovered_at__isnull=False), name='hrm_loan_installment_recovery_valid'),
        ]

    def clean(self):
        super().clean()
        loan = EmployeeLoan.objects.filter(pk=self.loan_id).first()
        if loan is None:
            return
        if self.month and self.month < loan.disbursed_on.replace(day=1):
            raise ValidationError({'month': 'An installment cannot be due before the loan was disbursed.'})
        existing = type(self).objects.filter(loan=loan).exclude(pk=self.pk).values_list('amount', flat=True)
        if self.amount is not None and sum(existing, self.amount) > loan.principal:
            raise ValidationError({'amount': 'The total scheduled installments cannot exceed the loan principal.'})
