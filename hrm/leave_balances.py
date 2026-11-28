from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from audit_log.service import bulk_create_with_audit, record_activity

from .attendance_policy import dates, effective_policy, holiday_dates, shift_for_date
from .models import (ApprovedLeaveDay, AttendancePolicy, Employee, LeaveBalance, LeaveBalanceMovement, LeaveRequest,
                     LeaveType, PayrollPeriod, PayrollRun, Payslip)


ZERO = Decimal('0.00')
DAY = Decimal('1.00')
CARRY = LeaveBalanceMovement.Bucket.CARRY
ANNUAL = LeaveBalanceMovement.Bucket.ANNUAL
RESERVING_RUNS = (PayrollRun.Status.CALCULATED, PayrollRun.Status.REVIEWED)
INACTIVE_RUNS = (PayrollRun.Status.CANCELLED, PayrollRun.Status.REJECTED)


def lock_employees(employee_ids):
    # Make the first database operation a write on SQLite as in manual attendance.
    return Employee.all_objects.filter(pk__in=employee_ids).update(updated_at=F('updated_at'))


def reservations(employee_ids, years, excluding=None):
    active = Payslip.objects.filter(employee_id__in=employee_ids, payroll_run__period__month__year__in=years,
                                   payroll_run__status__in=RESERVING_RUNS)
    if excluding is not None:
        month, currency = excluding
        active = active.exclude(payroll_run__period__month=month, payroll_run__currency_id=currency)
    reserved = defaultdict(lambda: ZERO)
    for inputs in active.values_list('calculation_inputs', flat=True):
        for debit in inputs.get('leave_coverage', []):
            reserved[debit['balance_id'], debit['bucket']] += Decimal(debit['days'])
    return reserved


class BalancePlan:
    def __init__(self, employee_ids, years, excluding=None):
        years = {year + offset for year in years for offset in (-1, 0, 1) if 1 <= year + offset <= 9999}
        self.balances = {(row.employee_id, row.leave_type_id, row.year): row for row in
                         LeaveBalance.objects.filter(employee_id__in=employee_ids, year__in=years)}
        self.used = reservations(employee_ids, years, excluding)

    def annual_remaining(self, balance):
        return balance.annual_quota_days - balance.annual_used_days - self.used[balance.pk, ANNUAL]

    def available_buckets(self, balance, day):
        next_balance = self.balances.get((balance.employee_id, balance.leave_type_id, balance.year + 1))
        prior_balance = self.balances.get((balance.employee_id, balance.leave_type_id, balance.year - 1))
        annual = self.annual_remaining(balance)
        active_carry = ZERO
        if balance.carry_finalized and day <= balance.carry_expires_on:
            awarded = min(balance.carried_days, self.annual_remaining(prior_balance)) if prior_balance else balance.carried_days
            active_carry = awarded - balance.carry_used_days - self.used[balance.pk, CARRY]
        if next_balance and next_balance.carry_finalized:
            # Preserve carry already committed in the following year when booking a prior-year debit.
            annual -= next_balance.carry_used_days + self.used[next_balance.pk, CARRY]
        return max(ZERO, active_carry), max(ZERO, annual)

    def debit(self, employee_id, leave_type_id, day, quantity):
        balance = self.balances.get((employee_id, leave_type_id, day.year))
        if balance is None:
            return [], quantity
        if not balance.carry_finalized and day <= balance.carry_expires_on:
            raise ValidationError({'leave_balance': f'Finalize carry-forward before using the balance on {day}.'})
        entries = []
        for bucket, available in zip((CARRY, ANNUAL), self.available_buckets(balance, day), strict=True):
            take = min(quantity, available)
            if take:
                self.used[balance.pk, bucket] += take
                entries.append({'balance_id': balance.pk, 'date': day.isoformat(), 'bucket': bucket, 'days': str(take)})
                quantity -= take
        return entries, quantity


def post_debits(entries, actor, *, leave=None, payslip=None):
    """Post a planned debit while holding the employee lock in the calling transaction."""
    if not entries:
        return
    source = f'leave:{leave.pk}' if leave else f'payroll:{payslip.payroll_run.period_id}:{payslip.employee_id}'
    movements = [LeaveBalanceMovement(
        balance_id=entry['balance_id'], effective_date=date.fromisoformat(entry['date']), bucket=entry['bucket'],
        quantity_days=Decimal(entry['days']), leave_request=leave, payslip=payslip,
        idempotency_key=f'{source}:{entry["date"]}:{entry["bucket"]}', create_by=actor, modified_by=actor,
    ) for entry in entries]
    totals = defaultdict(lambda: ZERO)
    for movement in movements:
        totals[movement.balance_id, movement.bucket] += movement.quantity_days
    annual_updates = []
    for (balance_id, bucket), amount in sorted(totals.items()):
        field = 'carry_used_days' if bucket == CARRY else 'annual_used_days'
        LeaveBalance.objects.filter(pk=balance_id).update(**{field: F(field) + amount}, modified_by=actor,
                                                           updated_at=timezone.now())
        if bucket == ANNUAL:
            annual_updates.append(balance_id)
    for prior in LeaveBalance.objects.filter(pk__in=annual_updates):
        successor = LeaveBalance.objects.filter(employee_id=prior.employee_id, leave_type_id=prior.leave_type_id,
                                               year=prior.year + 1, carry_finalized=True).first()
        if successor and successor.carried_days > prior.annual_quota_days - prior.annual_used_days:
            successor.carried_days = prior.annual_quota_days - prior.annual_used_days
            successor.modified_by = actor
            successor.save(update_fields=['carried_days', 'modified_by', 'updated_at'])
    bulk_create_with_audit(LeaveBalanceMovement.objects, movements)


@transaction.atomic
def allocate_leave_balances(year, actor, *, finalize_carry=False):
    start, end = date(year, 1, 1), date(year, 12, 31)
    today = timezone.localdate()
    employees = Employee.objects if year >= today.year else Employee.all_objects.filter(
        Q(is_archived=False) | Q(leaving_date__isnull=False))
    eligible = employees.filter(joining_date__lte=min(end, today)).filter(
        Q(leaving_date__isnull=True) | Q(leaving_date__gte=start))
    lock_employees(eligible.order_by().values('pk'))
    employee_ids = list(eligible.values_list('pk', flat=True))
    types = list(LeaveType.objects.filter(is_active=True, is_paid=True))
    prior_year = year - 1
    previous = {(row.employee_id, row.leave_type_id): row for row in LeaveBalance.objects.filter(
        employee_id__in=employee_ids, year=prior_year)}
    existing = {(row.employee_id, row.leave_type_id): row for row in LeaveBalance.objects.filter(
        employee_id__in=employee_ids, year=year)}
    rows = []
    for employee_id in employee_ids:
        for leave_type in types:
            key = (employee_id, leave_type.pk)
            if key not in existing:
                rows.append(LeaveBalance(employee_id=employee_id, leave_type=leave_type, year=year,
                    annual_quota_days=leave_type.annual_quota_days, carry_forward_limit_days=leave_type.carry_forward_limit_days,
                    carry_finalized=key not in previous or not leave_type.carry_forward_limit_days,
                    create_by=actor, modified_by=actor))
    created = bulk_create_with_audit(LeaveBalance.objects, rows) if rows else []
    pending = [row for row in [*existing.values(), *created] if not row.carry_finalized]
    finalized = 0
    if finalize_carry and pending:
        if year > today.year:
            raise ValidationError({'year': 'Carry-forward can be finalized after the prior calendar year has ended.'})
        committed = reservations(employee_ids, {prior_year})
        previous_requests = LeaveRequest.objects.filter(employee_id__in=employee_ids, start_date__year__lte=prior_year)
        unresolved = set(previous_requests.filter(
            Q(status=LeaveRequest.Status.PENDING, end_date__year=prior_year) |
            Q(status=LeaveRequest.Status.APPROVED, end_date__year__gte=prior_year, approved_days__isnull=True),
        ).values_list('employee_id', 'leave_type_id'))
        for row in pending:
            key = (row.employee_id, row.leave_type_id)
            prior = previous.get(key)
            if key in unresolved or (prior and any(committed[prior.pk, bucket] for bucket in (CARRY, ANNUAL))):
                continue
            unused = prior.annual_quota_days - prior.annual_used_days if prior else ZERO
            row.carried_days = min(row.carry_forward_limit_days, unused)
            row.carry_finalized = True
            row.modified_by = actor
            row.save(update_fields=['carried_days', 'carry_finalized', 'modified_by', 'updated_at'])
            finalized += 1
    return {'allocated': len(created), 'carry_finalized': finalized, 'carry_pending': len(pending) - finalized}


def calendar_days_for_leave(leave):
    selected = []
    start = max(leave.start_date, leave.employee.joining_date)
    end = min(leave.end_date, leave.employee.leaving_date or leave.end_date)
    if start > end:
        raise ValidationError({'start_date': 'The request is outside the employment period.'})
    snapshots = dict(PayrollPeriod.objects.filter(month__range=(start.replace(day=1), end.replace(day=1))).exclude(
        attendance_snapshot={}).values_list('month', 'attendance_snapshot'))
    policies = list(AttendancePolicy.objects.filter(effective_from__lte=end))
    assignments = list(leave.employee.schedule_assignments.filter(effective_from__lte=end).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=start)).select_related('schedule'))
    holidays = holiday_dates(start, end)
    excluded = holidays[None] | holidays[leave.employee.warehouse_id]
    prior_month = None
    for day in dates(start, end):
        month = day.replace(day=1)
        if month != prior_month:
            frozen = snapshots.get(month)
            policy = next((row for row in policies if row.effective_from <= day), None)
            prior_month = month
        if frozen:
            calendar = frozen['employees'].get(str(leave.employee_id))
            if calendar is None:
                raise ValidationError({'start_date': f'The frozen payroll calendar has no schedule for this employee on {day}.'})
            shift = calendar.get(day.isoformat())
        else:
            shift = shift_for_date(day, policy, assignments, excluded)
        if shift and shift.get('conflict'):
            raise ValidationError({'start_date': f'Reconcile overlapping schedules on {day} before approving leave.'})
        if shift:
            selected.append(day)
    if not selected:
        raise ValidationError({'start_date': 'The request has no scheduled working days in the employment period.'})
    return selected


def approve_leave_days(leave, actor, *, reconcile=False, paid=None, saved_dates=()):
    if not reconcile and not effective_policy(leave.end_date):
        return False
    if not reconcile and not effective_policy(leave.start_date):
        raise ValidationError({'start_date': 'Split this request at the first effective late-policy month.'})
    selected = calendar_days_for_leave(leave)
    if set(saved_dates).difference(selected):
        raise ValidationError({'start_date': 'The current calendar disagrees with a saved payslip for this request.'})
    if not reconcile:
        earlier = LeaveRequest.objects.filter(employee_id=leave.employee_id, leave_type_id=leave.leave_type_id,
                                               status=LeaveRequest.Status.PENDING).filter(
            Q(start_date__lt=leave.start_date) | Q(start_date=leave.start_date, pk__lt=leave.pk))
        if earlier.exists():
            raise ValidationError({'start_date': 'Decide earlier pending requests for this leave type first.'})
        if Payslip.objects.filter(employee_id=leave.employee_id,
                payroll_run__period__month__in={day.replace(day=1) for day in selected}).exclude(
                payroll_run__status__in=INACTIVE_RUNS).exists():
            raise ValidationError({'start_date': 'An active saved payslip already covers part of this request.'})
    overlap = ApprovedLeaveDay.objects.filter(employee_id=leave.employee_id, work_date__in=selected).first()
    if overlap:
        raise ValidationError({'start_date': f'This request overlaps approved leave on {overlap.work_date}.'})
    legacy = LeaveRequest.objects.filter(employee_id=leave.employee_id, status=LeaveRequest.Status.APPROVED,
        start_date__lte=max(selected), end_date__gte=min(selected), approved_days__isnull=True).exclude(pk=leave.pk)
    if any(old.start_date <= day <= old.end_date for old in legacy for day in selected):
        raise ValidationError({'start_date': 'Reconcile overlapping legacy approved leave before continuing.'})
    paid = leave.leave_type.is_paid if paid is None else paid
    debits = []
    if paid:
        plan = BalancePlan([leave.employee_id], {day.year for day in selected})
        for day in selected:
            if (leave.employee_id, leave.leave_type_id, day.year) not in plan.balances:
                raise ValidationError({'leave_balance': f'Allocate the {day.year} balance for {leave.leave_type.name}.'})
            entries, uncovered = plan.debit(leave.employee_id, leave.leave_type_id, day, DAY)
            if uncovered:
                raise ValidationError({'leave_balance': f'Insufficient paid leave on {day}. Submit unpaid leave separately.'})
            debits.extend(entries)
    bulk_create_with_audit(ApprovedLeaveDay.objects, [ApprovedLeaveDay(
        request=leave, employee_id=leave.employee_id, work_date=day, is_paid=paid, create_by=actor, modified_by=actor,
    ) for day in selected])
    post_debits(debits, actor, leave=leave)
    return True


@transaction.atomic
def reconcile_approved_leave(year, actor):
    start, end = date(year, 1, 1), date(year, 12, 31)
    legacy = LeaveRequest.objects.filter(status=LeaveRequest.Status.APPROVED, approved_days__isnull=True,
                                         start_date__lte=end, end_date__gte=start)
    lock_employees(legacy.order_by().values('employee_id'))
    # Require agreement with saved payroll evidence when it exists. Otherwise use the reviewed leave type.
    historical_paid = defaultdict(set)
    historical_dates = defaultdict(set)
    for inputs in Payslip.objects.filter(payroll_run__period__month__range=(start, end)).exclude(
        payroll_run__status__in=INACTIVE_RUNS).values_list('calculation_inputs', flat=True):
        for label, paid in (('paid_leave_days', True), ('unpaid_leave_days', False)):
            for row in inputs.get(label, []):
                historical_paid[row['leave_id']].add(paid)
                historical_dates[row['leave_id']].add(date.fromisoformat(row['date']))
    count = 0
    for leave in legacy.select_related('employee', 'leave_type').order_by('start_date', 'pk'):
        flags = historical_paid[leave.pk]
        if len(flags) > 1:
            raise ValidationError({'leave_balance': f'Saved payslips disagree on the paid status of leave request #{leave.pk}.'})
        try:
            approve_leave_days(leave, actor, reconcile=True, paid=next(iter(flags), None), saved_dates=historical_dates[leave.pk])
        except ValidationError as exc:
            raise ValidationError({'leave_balance': [f'Leave request #{leave.pk}: {message}' for message in exc.messages]}) from exc
        record_activity(leave, 'balance_reconciled', actor=actor)
        count += 1
    return count


@transaction.atomic
def prepare_leave_balances(year, actor, *, finalize_carry=False, reconcile=False):
    counts = allocate_leave_balances(year, actor, finalize_carry=finalize_carry)
    if reconcile:
        counts['reconciled'] = reconcile_approved_leave(year, actor)
    return counts
