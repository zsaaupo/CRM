"""Employee records, attendance, leave, and payroll models."""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from common.models import TimeStampedModel, Status

from .managers import EmployeeManager, EmployeeQuerySet
from .validators import validate_working_days


PAYROLL_MONEY = {'max_digits': 18, 'decimal_places': 4}
MIN_PAYROLL_AMOUNT = Decimal('0.0001')


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


class EmployeeSalary(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='salary_rates')
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT,
                                 related_name='employee_salaries')
    monthly_amount = models.DecimalField(**PAYROLL_MONEY, validators=[MinValueValidator(MIN_PAYROLL_AMOUNT)])
    effective_from = models.DateField(help_text='Add a new rate with a later effective date when the salary changes.')

    class Meta:
        ordering = ['employee_id', '-effective_from']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'effective_from'], name='hrm_salary_employee_date_unique'),
            models.CheckConstraint(condition=Q(monthly_amount__gt=0), name='hrm_salary_amount_positive'),
        ]

    def __str__(self):
        return f'{self.employee} - {self.currency_id} {self.monthly_amount} from {self.effective_from}'


class PayrollRun(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        APPROVED = 'approved', 'Approved'

    month = models.DateField(unique=True, help_text='Use the first calendar day of the payroll month.')
    currency = models.ForeignKey('inventory.Currency', to_field='code', on_delete=models.PROTECT,
                                 related_name='payroll_runs')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='approved_payroll_runs')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-month']
        permissions = [('approve_payrollrun', 'Can approve payroll runs')]
        constraints = [
            models.CheckConstraint(condition=Q(month__day=1), name='hrm_payroll_month_starts_on_first'),
            models.CheckConstraint(
                condition=Q(status='draft', approved_by__isnull=True, approved_at__isnull=True)
                          | Q(status='approved', approved_by__isnull=False, approved_at__isnull=False),
                name='hrm_payroll_approval_metadata',
            ),
        ]

    def __str__(self):
        return f'Payroll {self.month:%Y-%m}' if self.month else 'Payroll run'


class Payslip(TimeStampedModel):
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='payslips')
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='payslips')
    salary = models.ForeignKey(EmployeeSalary, on_delete=models.PROTECT, related_name='payslips')
    base_salary = models.DecimalField(**PAYROLL_MONEY, validators=[MinValueValidator(0)],
                                      help_text='Base salary used for this payslip, stored as a snapshot.')
    total_earnings = models.DecimalField(**PAYROLL_MONEY, validators=[MinValueValidator(0)])
    total_deductions = models.DecimalField(**PAYROLL_MONEY, default=0, validators=[MinValueValidator(0)])
    net_pay = models.DecimalField(**PAYROLL_MONEY, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ['-payroll_run_id', 'employee_id']
        constraints = [
            models.UniqueConstraint(fields=['payroll_run', 'employee'], name='hrm_payslip_run_employee_unique'),
            models.CheckConstraint(
                condition=Q(base_salary__gte=0, total_earnings__gte=F('base_salary'), total_deductions__gte=0,
                            total_deductions__lte=F('total_earnings'), net_pay__gte=0),
                name='hrm_payslip_amounts_valid',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        try:
            salary = self.salary if self.salary_id is not None else None
            run = self.payroll_run if self.payroll_run_id is not None else None
        except ObjectDoesNotExist:
            # Field validation reports a missing related record.
            salary = run = None
        if salary and salary.employee_id != self.employee_id:
            errors['salary'] = 'Choose a salary rate for this employee.'
        elif salary and run and salary.currency_id != run.currency_id:
            errors['salary'] = 'The salary rate must use the payroll currency.'
        elif salary and run and salary.effective_from.replace(day=1) > run.month:
            errors['salary'] = 'The salary rate starts after this payroll month.'

        totals = (self.total_earnings, self.total_deductions, self.net_pay)
        if all(isinstance(value, Decimal) and value.is_finite() for value in totals):
            if self.net_pay != self.total_earnings - self.total_deductions:
                errors['net_pay'] = 'Net pay must equal earnings minus deductions.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.employee} - {self.payroll_run}'


class PayslipLine(TimeStampedModel):
    class Kind(models.TextChoices):
        EARNING = 'earning', 'Earning'
        DEDUCTION = 'deduction', 'Deduction'

    payslip = models.ForeignKey(Payslip, on_delete=models.PROTECT, related_name='lines')
    kind = models.CharField(max_length=16, choices=Kind.choices)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(**PAYROLL_MONEY, validators=[MinValueValidator(MIN_PAYROLL_AMOUNT)])

    class Meta:
        ordering = ['pk']
        constraints = [models.CheckConstraint(condition=Q(kind__in=['earning', 'deduction'], amount__gt=0),
                                              name='hrm_payslip_line_valid')]

    def __str__(self):
        return self.description


class SalaryPayment(TimeStampedModel):
    payslip = models.ForeignKey(Payslip, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(**PAYROLL_MONEY, validators=[MinValueValidator(MIN_PAYROLL_AMOUNT)])
    paid_on = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-paid_on', '-pk']
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name='hrm_salary_payment_positive'),
            models.UniqueConstraint(fields=['payslip', 'reference'], condition=~Q(reference=''),
                                    name='hrm_salary_payment_reference_unique'),
        ]

    def clean(self):
        super().clean()
        if self.payslip_id is None:
            return
        try:
            payslip = self.payslip
        except Payslip.DoesNotExist:
            return
        if payslip.payroll_run.status != PayrollRun.Status.APPROVED:
            raise ValidationError({'payslip': 'The payroll run must be approved before recording payment.'})
        if isinstance(self.amount, Decimal) and self.amount.is_finite():
            other_payments = payslip.payments.exclude(pk=self.pk).values_list('amount', flat=True)
            if sum(other_payments, Decimal('0')) + self.amount > payslip.net_pay:
                raise ValidationError({'amount': 'Payments cannot exceed the payslip net pay.'})

    def __str__(self):
        return f'{self.payslip} - {self.amount} paid on {self.paid_on}'
