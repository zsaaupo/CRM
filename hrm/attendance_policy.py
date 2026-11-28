from calendar import monthrange
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Prefetch, Q
from django.utils import timezone

from .models import AttendancePolicy, Employee, EmployeeScheduleAssignment, Holiday, PayrollPeriod


def dates(start, end):
    return (start + timedelta(days=offset) for offset in range((end - start).days + 1))


def effective_policy(day):
    return AttendancePolicy.objects.filter(effective_from__lte=day).first()


def period_calendar(month):
    return PayrollPeriod.objects.filter(month=month).values_list('attendance_snapshot', flat=True).first() or {}


def shift_for_date(day, policy, assignments, holidays):
    if day in holidays:
        return None
    schedules = [assignment.schedule for assignment in assignments if assignment.effective_from <= day and (
        assignment.effective_to is None or day <= assignment.effective_to)]
    if len(schedules) > 1:
        return {'conflict': True}
    shift = schedules[0] if schedules else policy
    if shift is None or day.weekday() not in shift.working_days:
        return None
    return {
        'schedule_id': schedules[0].pk if schedules else None,
        'start': shift.start_time.isoformat(), 'end': shift.end_time.isoformat(),
        'grace': shift.grace_minutes, 'break_minutes': schedules[0].break_minutes if schedules else 0,
    }


def holiday_dates(start, end):
    holidays = defaultdict(set)
    for warehouse, day in Holiday.objects.filter(date__range=(start, end)).values_list('warehouse_id', 'date'):
        holidays[warehouse].add(day)
    return holidays


def calendar_snapshot(month, policy):
    end = month.replace(day=monthrange(month.year, month.month)[1])
    assignments = EmployeeScheduleAssignment.objects.filter(effective_from__lte=end).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=month)).select_related('schedule')
    employees = Employee.all_objects.filter(joining_date__lte=end).filter(
        Q(leaving_date__isnull=True) | Q(leaving_date__gte=month)).filter(
        Q(is_archived=False) | Q(leaving_date__isnull=False)).prefetch_related(
        Prefetch('schedule_assignments', queryset=assignments, to_attr='month_assignments'))
    holidays = holiday_dates(month, end)
    calendars = {}
    for employee in employees:
        excluded = holidays[None] | holidays[employee.warehouse_id]
        calendars[str(employee.pk)] = {
            day.isoformat(): shift for day in dates(month, end)
            if (shift := shift_for_date(day, policy, employee.month_assignments, excluded)) is not None
        }
    return {
        'timezone': timezone.get_current_timezone_name(),
        'policy': {
            'id': policy.pk, 'effective_from': policy.effective_from.isoformat(),
            'start_time': policy.start_time.isoformat(), 'end_time': policy.end_time.isoformat(),
            'working_days': policy.working_days, 'grace_minutes': policy.grace_minutes,
            'monthly_late_allowance': policy.monthly_late_allowance,
            'late_marks_per_leave_day': policy.late_marks_per_leave_day,
            'early_leave_threshold_minutes': policy.early_leave_threshold_minutes,
            'penalty_leave_type_id': policy.penalty_leave_type_id,
            'deduction_basis': policy.deduction_basis, 'deduction_divisor': policy.deduction_divisor,
            'deduct_absences': policy.deduct_absences,
        },
        'employees': calendars,
    }


def live_shift(employee, day, policy):
    assignments = employee.schedule_assignments.filter(effective_from__lte=day).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=day)).select_related('schedule')
    holidays = holiday_dates(day, day)
    return shift_for_date(day, policy, assignments, holidays[None] | holidays[employee.warehouse_id])


def classify_attendance(record):
    """Keep the recorded schedule when punches on the same employee/date are corrected."""
    day = record._meta.get_field('work_date').to_python(record.work_date)
    if not day or not record.employee_id:
        return
    identity = {'employee_id': record.employee_id, 'work_date': day.isoformat()}
    if not all(record.schedule_snapshot.get(key) == value for key, value in identity.items()):
        month = day.replace(day=1)
        frozen = period_calendar(month)
        policy = effective_policy(day) if not frozen else None
        record.scheduled_start = record.scheduled_end = record.is_late = None
        record.schedule_snapshot = {}
        if not frozen and policy is None:
            return
        if frozen:
            calendar = frozen['employees'].get(str(record.employee_id))
            shift = {'conflict': True} if calendar is None else calendar.get(day.isoformat())
        else:
            shift = live_shift(record.employee, day, policy)
        zone = frozen.get('timezone') or timezone.get_current_timezone_name()
        record.schedule_snapshot = {**identity, 'timezone': zone, **(shift or {})}
        if shift and not shift.get('conflict'):
            start, end = time.fromisoformat(shift['start']), time.fromisoformat(shift['end'])
            end_day = day + timedelta(days=end < start)
            record.scheduled_start = datetime.combine(day, start, ZoneInfo(zone))
            record.scheduled_end = datetime.combine(end_day, end, ZoneInfo(zone))
    if record.scheduled_start and record.check_in:
        cutoff = record.scheduled_start + timedelta(minutes=record.schedule_snapshot['grace'])
        punch = record._meta.get_field('check_in').to_python(record.check_in)
        if timezone.is_naive(punch):
            punch = timezone.make_aware(punch, ZoneInfo(record.schedule_snapshot['timezone']))
        record.is_late = punch > cutoff
    else:
        record.is_late = None
