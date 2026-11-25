"""HR-02 services: annual leave allocation, carry expiry, balance consumption/reversal, late policy, payroll month freeze."""

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from account.permissions import has_role_permissions
from common.idempotency import IdempotentResult, run_idempotent
from .models import (
    Attendance, Employee, EmployeeAttendanceStatus, EmployeeScheduleAssignment, Holiday,
    LatePolicy, LeaveBalance, LeaveBalanceEntry, LeavePolicy, LeaveRequest, LeaveType,
    PayrollMonthPolicy, PayrollPeriod, PayrollRun, Payslip,
)
from .workforce import holiday_dates_for_employee, is_working_day, resolve_global_weekly_off


ZERO = Decimal('0')
FINALIZED_STATUSES = (PayrollRun.Status.APPROVED, PayrollRun.Status.PAYMENT_PROCESSING, PayrollRun.Status.PAID)


def resolve_leave_policy(leave_type: LeaveType, year: int) -> tuple[Decimal, Decimal, str]:
    pol = (
        LeavePolicy.objects.filter(leave_type=leave_type, effective_from__lte=date(year, 1, 1))
        .order_by('-effective_from', '-pk')
        .first()
    )
    if pol:
        return Decimal(pol.annual_quota), Decimal(pol.carry_forward_limit), pol.carry_expiry_month_day or '03-31'
    return Decimal(leave_type.annual_quota or 0), Decimal(leave_type.carry_forward_limit or 0), '03-31'


def allocate_annual_leave(year: int, *, employee_ids: list[int] | None = None, request_key=None, actor=None) -> tuple[dict, bool]:
    if actor is not None and not actor.is_superuser and not has_role_permissions(actor, ('hrm.allocate_leave',)):
        raise PermissionDenied('Permission hrm.allocate_leave is required.')
    rk = request_key or uuid.uuid5(uuid.NAMESPACE_DNS, f'allocate-leave-{year}-{sorted(employee_ids or [])}')
    actor_name = actor.get_username() if actor else 'system'

    def _do():
        created_count = 0
        skipped_count = 0
        with transaction.atomic():
            emps = Employee.objects.filter(pk__in=employee_ids) if employee_ids else Employee.objects.all()
            leave_types = [lt for lt in LeaveType.objects.filter(is_active=True)]
            for emp in emps.order_by('pk'):
                for lt in leave_types:
                    quota, carry_limit, _ = resolve_leave_policy(lt, year)
                    if quota <= ZERO and carry_limit <= ZERO:
                        continue
                    existing = LeaveBalance.objects.select_for_update().filter(
                        employee=emp, leave_type=lt, year=year,
                    ).first()
                    if existing is not None:
                        skipped_count += 1
                        continue

                    prev = LeaveBalance.objects.filter(employee=emp, leave_type=lt, year=year - 1).first()
                    if prev is None:
                        prior_known = False
                        carried = ZERO
                    else:
                        prior_known = True
                        unused_prev = max(ZERO, Decimal(prev.allocated) + Decimal(prev.carried) - Decimal(prev.used) - Decimal(prev.expired))
                        carried = min(unused_prev, carry_limit)

                    bal = LeaveBalance.objects.create(
                        employee=emp, leave_type=lt, year=year,
                        allocated=quota, carried=carried, prior_year_known=prior_known,
                        create_by=actor_name, modified_by=actor_name,
                    )
                    LeaveBalanceEntry.objects.create(
                        balance=bal,
                        kind=LeaveBalanceEntry.Kind.ALLOCATION,
                        days=quota,
                        breakdown={'carry': '0', 'current': str(quota)},
                        effective_date=date(year, 1, 1),
                        request_key=f'alloc:{year}:{emp.pk}:{lt.pk}',
                        create_by=actor_name,
                        modified_by=actor_name,
                    )
                    if carried > ZERO:
                        LeaveBalanceEntry.objects.create(
                            balance=bal,
                            kind=LeaveBalanceEntry.Kind.CARRY,
                            days=carried,
                            breakdown={'carry': str(carried), 'current': '0'},
                            effective_date=date(year, 1, 1),
                            request_key=f'carry:{year}:{emp.pk}:{lt.pk}',
                            create_by=actor_name,
                            modified_by=actor_name,
                        )
                    created_count += 1
            res = {'year': year, 'created': created_count, 'skipped': skipped_count}
            return IdempotentResult('leave_allocation', year, res, value=res)

    return run_idempotent(
        rk, f'allocate_leave:{year}', {'year': year, 'employee_ids': sorted(employee_ids or [])}, _do,
        loader=lambda r: r.response_payload,
    )


def expire_carry_forward(year: int, *, as_of_date: date | None = None, actor=None) -> int:
    as_of = as_of_date or timezone.localdate()
    actor_name = actor.get_username() if actor else 'system'
    expired_count = 0
    with transaction.atomic():
        balances = LeaveBalance.objects.select_for_update().filter(year=year).select_related('leave_type')
        for bal in balances:
            _, _, expiry_md = resolve_leave_policy(bal.leave_type, year)
            mm, dd = (int(x) for x in expiry_md.split('-'))
            cutoff = date(year, mm, dd)
            if as_of <= cutoff:
                continue
            rem_carry = bal.remaining_carry
            if rem_carry <= ZERO:
                continue
            req_key = f'expiry:{bal.pk}:{year}'
            if LeaveBalanceEntry.objects.filter(request_key=req_key).exists():
                continue
            bal.expired = Decimal(bal.expired) + rem_carry
            bal.modified_by = actor_name
            bal.save(update_fields=['expired', 'modified_by', 'updated_at'])
            LeaveBalanceEntry.objects.create(
                balance=bal,
                kind=LeaveBalanceEntry.Kind.EXPIRY,
                days=rem_carry,
                breakdown={'carry': str(rem_carry), 'current': '0'},
                effective_date=as_of,
                request_key=req_key,
                create_by=actor_name,
                modified_by=actor_name,
            )
            expired_count += 1
    return expired_count


def _leave_year_segments(employee: Employee, start_date: date, end_date: date) -> dict[int, list[date]]:
    holidays = holiday_dates_for_employee(employee, start_date, end_date)
    segs = defaultdict(list)
    cur = start_date
    while cur <= end_date:
        if is_working_day(employee, cur, holidays):
            segs[cur.year].append(cur)
        cur += timedelta(days=1)
    return segs


def _finalized_month_conflict(start_date: date, end_date: date) -> str | None:
    cur = start_date.replace(day=1)
    end_m = end_date.replace(day=1)
    months = []
    while cur <= end_m:
        months.append(cur)
        cur = date(cur.year + (1 if cur.month == 12 else 0), 1 if cur.month == 12 else cur.month + 1, 1)
    if PayrollRun.objects.filter(period__month__in=months, status__in=FINALIZED_STATUSES).exists():
        return 'Cannot review leave covering a finalized payroll month.'
    return None


def apply_leave_approval_balance_checks(leave: LeaveRequest, actor) -> None:
    """Called inside review_leave_request transaction when approving a request."""
    conflict_msg = _finalized_month_conflict(leave.start_date, leave.end_date)
    if conflict_msg:
        raise DjangoValidationError({'start_date': conflict_msg})

    # Chronological review within (employee, leave_type) when balance tracking applies
    years = range(leave.start_date.year, leave.end_date.year + 1)
    has_tracking = LeaveBalance.objects.filter(
        employee=leave.employee, leave_type=leave.leave_type, year__in=years,
    ).exists() or any(resolve_leave_policy(leave.leave_type, y)[0] > ZERO for y in years)
    if not has_tracking:
        return

    earlier_pending = LeaveRequest.objects.filter(
        employee=leave.employee,
        leave_type=leave.leave_type,
        status=LeaveRequest.Status.PENDING,
        start_date__lt=leave.start_date,
    ).exclude(pk=leave.pk).exists()
    if earlier_pending:
        raise DjangoValidationError({'start_date': 'Review earlier pending leave requests of this leave type first.'})

    segs = _leave_year_segments(leave.employee, leave.start_date, leave.end_date)
    if not segs:
        return

    # Lock LeaveBalance rows for all affected years (canonical lock ②)
    balances = {}
    for yr in sorted(segs.keys()):
        LeaveBalance.objects.filter(employee=leave.employee, leave_type=leave.leave_type, year=yr).update(updated_at=F('updated_at'))
        bal = LeaveBalance.objects.select_for_update().filter(
            employee=leave.employee, leave_type=leave.leave_type, year=yr,
        ).first()
        if bal is None:
            raise DjangoValidationError({'leave_type': f'No leave balance allocated for {yr}.'})
        balances[yr] = bal

    actor_name = actor.get_username()
    for yr, days_list in sorted(segs.items()):
        bal = balances[yr]
        _, _, expiry_md = resolve_leave_policy(leave.leave_type, yr)
        mm, dd = (int(x) for x in expiry_md.split('-'))
        cutoff = date(yr, mm, dd)

        pre_expiry_count = Decimal(sum(1 for d in days_list if d <= cutoff))
        post_expiry_count = Decimal(sum(1 for d in days_list if d > cutoff))

        rem_carry = bal.remaining_carry
        rem_current = bal.remaining_current

        # Consume valid carry only for days <= cutoff (D-50)
        carry_take = min(rem_carry, pre_expiry_count)
        needed_from_current = (pre_expiry_count - carry_take) + post_expiry_count
        if needed_from_current > rem_current:
            raise DjangoValidationError({'leave_type': 'Insufficient leave balance for this request.'})

        total_taken = carry_take + needed_from_current
        bal.carry_used = Decimal(bal.carry_used) + carry_take
        bal.used = Decimal(bal.used) + total_taken
        bal.modified_by = actor_name
        bal.save(update_fields=['carry_used', 'used', 'modified_by', 'updated_at'])

        LeaveBalanceEntry.objects.create(
            balance=bal,
            kind=LeaveBalanceEntry.Kind.USAGE,
            days=total_taken,
            leave_request=leave,
            breakdown={'carry': str(carry_take), 'current': str(needed_from_current)},
            effective_date=days_list[0],
            request_key=f'leave_usage:{leave.pk}:{yr}',
            create_by=actor_name,
            modified_by=actor_name,
        )


def reverse_leave_balance(leave: LeaveRequest, actor) -> None:
    actor_name = actor.get_username()
    with transaction.atomic():
        usages = LeaveBalanceEntry.objects.filter(
            leave_request=leave, kind=LeaveBalanceEntry.Kind.USAGE,
        ).select_related('balance').order_by('pk')
        for entry in usages:
            rev_key = f'leave_rev:{entry.pk}'
            if LeaveBalanceEntry.objects.filter(request_key=rev_key).exists():
                continue
            bal = LeaveBalance.objects.select_for_update().get(pk=entry.balance_id)
            c_part = Decimal(entry.breakdown.get('carry', '0'))
            cur_part = Decimal(entry.breakdown.get('current', '0'))
            bal.carry_used = max(ZERO, Decimal(bal.carry_used) - c_part)
            bal.used = max(ZERO, Decimal(bal.used) - (c_part + cur_part))
            bal.modified_by = actor_name
            bal.save(update_fields=['carry_used', 'used', 'modified_by', 'updated_at'])
            LeaveBalanceEntry.objects.create(
                balance=bal,
                kind=LeaveBalanceEntry.Kind.REVERSAL,
                days=entry.days,
                leave_request=leave,
                breakdown={'carry': str(c_part), 'current': str(cur_part)},
                effective_date=timezone.localdate(),
                request_key=rev_key,
                create_by=actor_name,
                modified_by=actor_name,
            )


def _late_policy_for_month(month_first_day: date) -> dict | None:
    """Return frozen snapshot policy for month if present, else active LatePolicy <= month end."""
    frozen = PayrollMonthPolicy.objects.filter(month=month_first_day).first()
    if frozen and 'late_policy' in (frozen.snapshot or {}):
        return frozen.snapshot['late_policy']
    end = month_first_day.replace(day=monthrange(month_first_day.year, month_first_day.month)[1])
    lp = LatePolicy.objects.filter(effective_from__lte=end).order_by('-effective_from', '-pk').first()
    if lp is None:
        return None
    return {
        'grace_minutes': lp.grace_minutes,
        'monthly_late_allowance': lp.monthly_late_allowance,
        'late_marks_per_leave_day': lp.late_marks_per_leave_day,
        'early_leave_threshold_minutes': lp.early_leave_threshold_minutes,
    }


def freeze_payroll_month_policy(month: date, actor) -> PayrollMonthPolicy:
    """D-52: freeze policy snapshot on first committed payroll calculation for the month."""
    month = month.replace(day=1)
    PayrollPeriod.objects.filter(month=month).update(month=F('month'))
    existing = PayrollMonthPolicy.objects.select_for_update().filter(month=month).first()
    if existing is not None:
        return existing

    # Check if conflicting legacy finalized slips exist without a policy snapshot
    legacy_finalized = Payslip.objects.filter(
        payroll_run__period__month=month,
        payroll_run__status__in=FINALIZED_STATUSES,
    ).exists()
    if legacy_finalized:
        raise ValueError('Conflicting finalized payslips already exist for this month without a PayrollMonthPolicy snapshot.')

    end = month.replace(day=monthrange(month.year, month.month)[1])
    lp = LatePolicy.objects.filter(effective_from__lte=end).order_by('-effective_from', '-pk').first()
    snapshot = {
        'weekly_off_days': resolve_global_weekly_off(month),
        'late_policy': {
            'grace_minutes': lp.grace_minutes,
            'monthly_late_allowance': lp.monthly_late_allowance,
            'late_marks_per_leave_day': lp.late_marks_per_leave_day,
            'early_leave_threshold_minutes': lp.early_leave_threshold_minutes,
        } if lp else None,
        'frozen_month': month.isoformat(),
    }
    username = actor.get_username() if actor else 'system'
    return PayrollMonthPolicy.objects.create(
        month=month,
        snapshot=snapshot,
        calc_version='v2',
        frozen_at=timezone.now(),
        frozen_by=actor,
        create_by=username,
        modified_by=username,
    )


def compute_late_policy_deduction(employee, payable_days: dict, holidays: set[date], divisor: int, month: date) -> tuple[Decimal, dict | None]:
    """D-51: max one penalty mark per scheduled workday (precedence: absence > unpaid leave > late > early_leave)."""
    policy = _late_policy_for_month(month)
    if not policy:
        return ZERO, None

    ratio = int(policy.get('late_marks_per_leave_day') or 0)
    if ratio <= 0:
        return ZERO, None
    allowance = int(policy.get('monthly_late_allowance') or 0)
    grace = int(policy.get('grace_minutes') or 0)
    early_thresh = int(policy.get('early_leave_threshold_minutes') or 0)

    schedules = defaultdict(list)
    start, end = min(payable_days), max(payable_days)
    for assignment in getattr(employee, 'payroll_schedules', []):
        first = max(start, assignment.effective_from)
        last = min(end, assignment.effective_to or end)
        cur = first
        while cur <= last:
            schedules[cur].append(assignment.schedule)
            cur += timedelta(days=1)

    leaves = {}
    for lv in getattr(employee, 'payroll_leave', []):
        cur = max(start, lv.start_date)
        last = min(end, lv.end_date)
        while cur <= last:
            leaves[cur] = lv
            cur += timedelta(days=1)

    attendance = {row.work_date: row for row in getattr(employee, 'payroll_attendance', [])}
    late_dates = []
    early_dates = []
    basic_ref = Decimal('0')

    for day, comp in sorted(payable_days.items()):
        basic_ref = comp.basic_salary
        if day in holidays:
            continue
        day_scheds = schedules.get(day, [])
        if len(day_scheds) != 1:
            continue
        sched = day_scheds[0]
        if day.weekday() not in sched.working_days:
            continue
        if day in leaves:
            continue
        att = attendance.get(day)
        if not att or att.status_id != EmployeeAttendanceStatus.Code.PRESENT or not att.check_in:
            continue

        local_in = timezone.localtime(att.check_in) if timezone.is_aware(att.check_in) else att.check_in
        sched_start = datetime.combine(day, sched.start_time, tzinfo=local_in.tzinfo)
        eff_grace = sched.grace_minutes or grace
        if local_in > sched_start + timedelta(minutes=eff_grace):
            late_dates.append(day.isoformat())
            continue  # max one mark per day: late takes precedence over early-leave

        eff_early = sched.early_leave_threshold_minutes or early_thresh
        if eff_early > 0 and att.check_out:
            local_out = timezone.localtime(att.check_out) if timezone.is_aware(att.check_out) else att.check_out
            end_day = day + timedelta(days=1) if sched.is_overnight_shift else day
            sched_end = datetime.combine(end_day, sched.end_time, tzinfo=local_out.tzinfo)
            if local_out < sched_end - timedelta(minutes=eff_early):
                early_dates.append(day.isoformat())

    total_marks = len(late_dates) + len(early_dates)
    chargeable_marks = max(0, total_marks - allowance)
    derived_days = chargeable_marks // ratio
    if derived_days <= 0:
        return ZERO, None
    deduction = (basic_ref / divisor) * derived_days
    return deduction, {
        'late_dates': late_dates,
        'early_dates': early_dates,
        'allowance': allowance,
        'ratio': ratio,
        'derived_days': derived_days,
        'calendar_days': divisor,
    }
