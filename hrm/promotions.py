from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import BigIntegerField, Case, CharField, F, OuterRef, Q, Subquery, When
from django.db.models.functions import TruncDate
from django.utils import timezone

from audit_log.service import record_activity

from .models import Designation, Employee, EmployeeDesignationChange


HISTORY = EmployeeDesignationChange.Kind
ELIGIBLE_STATUSES = ('active', 'on-leave')


class DesignationConflict(Exception):
    pass


def business_date():
    return timezone.localdate(timezone=timezone.get_default_timezone())


def eligible_employees():
    return Employee.objects.filter(status_id__in=ELIGIBLE_STATUSES)


def valid_history_dates():
    archive_date = TruncDate('employee__archived_at', tzinfo=timezone.get_default_timezone())
    employed = (Q(effective_date__gte=F('employee__joining_date'))
                & (Q(employee__leaving_date__isnull=True) | Q(effective_date__lte=F('employee__leaving_date')))
                & (Q(employee__archived_at__isnull=True) | Q(effective_date__lte=archive_date)))
    return Q(kind=HISTORY.OPENING) | employed


def effective_history():
    return EmployeeDesignationChange.objects.filter(valid_history_dates(), superseded_by__isnull=True).exclude(kind=HISTORY.CANCEL)


def with_effective_designation(queryset, day=None):
    day = day or business_date()
    history = effective_history().filter(employee_id=OuterRef('pk'), effective_date__lte=day)
    return queryset.alias(effective_designation_entry=Subquery(history.values('pk')[:1])).annotate(
        effective_designation_id=Case(When(effective_designation_entry__isnull=False,
            then=Subquery(history.values('designation_id')[:1])), default=F('designation_id'), output_field=BigIntegerField()),
        effective_designation_name=Case(When(effective_designation_entry__isnull=False,
            then=Subquery(history.values('designation__name')[:1])), default=F('designation__name'), output_field=CharField()),
    )


def with_previous_designation(queryset):
    earlier = effective_history().filter(employee_id=OuterRef('employee_id')).filter(
        Q(effective_date__lt=OuterRef('effective_date')) |
        Q(effective_date=OuterRef('effective_date'), kind=HISTORY.OPENING))
    return queryset.annotate(effective_previous_name=Subquery(earlier.values('designation_name')[:1]))


def create_opening_designation(employee, actor=None):
    return EmployeeDesignationChange.objects.create(
        employee=employee, designation=employee.designation, effective_date=employee.joining_date,
        employee_name=employee.full_name, employee_code=employee.employee_code, kind=HISTORY.OPENING,
        designation_name=employee.designation.name if employee.designation_id else '', recorded_by=actor,
    )


def lock_employee(employee_id):
    employees = Employee.all_objects.filter(pk=employee_id)
    if not employees.update(updated_at=F('updated_at')):
        raise ValidationError({'employee_id': 'Choose a current employee.'})
    return employees.select_related('designation').get()


def preceding_designation(employee, day):
    history = effective_history().filter(employee=employee).filter(
        Q(effective_date__lt=day) | Q(effective_date=day, kind=HISTORY.OPENING))
    preceding = history.select_related('designation').first()
    return preceding.designation if preceding else None


def validate_change(employee, designation_id, day, opening):
    if employee.is_archived or employee.status_id not in ELIGIBLE_STATUSES:
        raise ValidationError({'employee_id': 'Choose a current employee with Active or On leave status.'})
    earliest = max(employee.joining_date, opening.effective_date)
    if day < earliest or (employee.leaving_date and day > employee.leaving_date):
        raise ValidationError({'effective_date': 'Choose a date within recorded employment and designation history.'})
    choices = Designation.objects.filter(pk=designation_id, is_active=True)
    if not choices.update(updated_at=F('updated_at')):
        raise ValidationError({'designation_id': 'Choose an active designation.'})
    return choices.get()


def sync_employee_designation(employee, actor, *, day=None, log_application=False):
    if employee.is_archived:
        return False
    latest = effective_history().filter(employee=employee, effective_date__lte=day or business_date()).first()
    if latest is None or latest.designation_id == employee.designation_id:
        return False
    employee.designation_id = latest.designation_id
    employee.modified_by = actor
    employee.save(update_fields=['designation', 'modified_by', 'updated_at'])
    if log_application:
        record_activity(latest, 'applied', actor=actor)
    return True


def append_change(employee, designation, previous, day, remarks, actor, kind, supersedes=None):
    entry = EmployeeDesignationChange.objects.create(
        employee=employee, designation=designation, previous_designation=previous, effective_date=day,
        employee_code=employee.employee_code, employee_name=employee.full_name,
        designation_name=designation.name if designation else '',
        recorded_previous_designation_name=previous.name if previous else '',
        remarks=remarks, recorded_by=actor, kind=kind, supersedes=supersedes,
    )
    sync_employee_designation(employee, actor.get_username())
    return entry


@transaction.atomic
def record_designation_change(employee_id, designation_id, effective_date, remarks, actor):
    employee = lock_employee(employee_id)
    history = employee.designation_history
    opening = history.filter(kind=HISTORY.OPENING).first() or create_opening_designation(employee)
    designation = validate_change(employee, designation_id, effective_date, opening)
    if history.filter(kind=HISTORY.CHANGE, effective_date=effective_date).exists():
        raise DesignationConflict('A change already exists on this date. Record a correction to its latest entry.')
    previous = preceding_designation(employee, effective_date)
    if previous == designation:
        raise ValidationError({'designation_id': 'This designation is already effective on that date.'})
    return append_change(employee, designation, previous, effective_date, remarks, actor, HISTORY.CHANGE)


@transaction.atomic
def correct_designation_change(target, designation_id, remarks, actor):
    employee = lock_employee(target.employee_id)
    if target.kind == HISTORY.OPENING or employee.designation_history.filter(supersedes=target).exists():
        raise DesignationConflict('Choose the latest revision of a designation change.')
    previous = preceding_designation(employee, target.effective_date)
    if designation_id is None:
        if target.kind == HISTORY.CANCEL:
            raise DesignationConflict('This change is already cancelled.')
        return append_change(employee, None, target.designation, target.effective_date, remarks, actor, HISTORY.CANCEL, target)
    opening = employee.designation_history.get(kind=HISTORY.OPENING)
    designation = validate_change(employee, designation_id, target.effective_date, opening)
    if designation in (target.designation, previous):
        raise ValidationError({'designation_id': 'Choose a different designation or cancel the existing change.'})
    return append_change(employee, designation, target.designation or previous, target.effective_date, remarks, actor,
                         HISTORY.REPLACE, target)


def sync_due_designations():
    today = business_date()
    due = EmployeeDesignationChange.objects.filter(effective_date__lte=today).exclude(kind=HISTORY.OPENING)
    candidates = with_effective_designation(Employee.objects.filter(pk__in=due.order_by().values('employee_id')), today)
    stale = candidates.filter(
        Q(designation__isnull=True, effective_designation_id__isnull=False)
        | Q(designation__isnull=False, effective_designation_id__isnull=True)
        | (Q(designation__isnull=False, effective_designation_id__isnull=False) & ~Q(designation_id=F('effective_designation_id'))))
    count = 0
    for employee_id in list(stale.order_by('pk').values_list('pk', flat=True)):
        with transaction.atomic():
            employee = lock_employee(employee_id)
            count += sync_employee_designation(employee, 'sync_employee_designations', day=today, log_application=True)
    return count
