"""HR-01 & HR-03 services: promotions, notices, calendar, device attendance import, advances, increments, manual vouchers."""

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import csv
from io import StringIO
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from account.permissions import has_role_permissions
from accounting.models import AccountTransaction, FinancialAccount
from accounting.services import check_active_account, locked_accounts, post_transaction
from common.audit import log_audit_event
from common.idempotency import IdempotentResult, run_idempotent
from common.models import AuditLog
from common.money import q_money
from warehouse.scope import warehouse_scope
from .models import (
    Attendance, AttendanceImportBatch, AttendanceImportRow, Employee, EmployeeAttendanceStatus,
    EmployeeCompensation, EmployeeLoan, EmployeePromotion, EmployeeScheduleAssignment, Holiday,
    LeaveRequest, LoanInstallment, ManualPayrollVoucher, Notice, PayrollPeriod, Payslip, SalaryAdvance,
    SalaryIncrement, SalaryPayment, WorkSchedule, WorkingCalendar,
)


ZERO = Decimal('0')


# ---------------- Promotions ----------------


def record_promotion(actor, *, employee_id: int, effective_date: date, new_designation_id=None,
                     new_department_id=None, salary_increment_amount=0, reason='', notes='') -> EmployeePromotion:
    if not has_role_permissions(actor, ('hrm.approve_promotion',)):
        raise PermissionDenied('Permission hrm.approve_promotion is required.')
    with transaction.atomic():
        Employee.all_objects.filter(pk=employee_id).update(updated_at=F('updated_at'))
        emp = Employee.objects.filter(pk=employee_id).first()
        if emp is None:
            raise NotFound('Employee not found.')
        username = actor.get_username()
        promo = EmployeePromotion.objects.create(
            employee=emp,
            previous_designation_id=emp.designation_id,
            new_designation_id=new_designation_id,
            previous_department_id=emp.department_id,
            new_department_id=new_department_id,
            effective_date=effective_date,
            salary_increment_amount=q_money(salary_increment_amount or 0),
            reason=reason or '',
            notes=notes or '',
            created_by=actor,
            create_by=username,
            modified_by=username,
        )
        log_audit_event(actor, AuditLog.Action.CREATE, promo, changes={'effective_date': effective_date.isoformat()})
        return promo


def apply_due_promotions(as_of_date: date | None = None, *, actor=None) -> int:
    cutoff = as_of_date or timezone.localdate()
    applied = 0
    with transaction.atomic():
        due = list(
            EmployeePromotion.objects.select_for_update()
            .filter(applied_at__isnull=True, effective_date__lte=cutoff)
            .select_related('employee')
            .order_by('effective_date', 'pk')
        )
        now = timezone.now()
        actor_name = actor.get_username() if actor else 'system'
        for promo in due:
            emp = Employee.objects.select_for_update().get(pk=promo.employee_id)
            update_fields = ['modified_by', 'updated_at']
            if promo.new_designation_id is not None:
                emp.designation_id = promo.new_designation_id
                update_fields.append('designation')
            if promo.new_department_id is not None:
                emp.department_id = promo.new_department_id
                update_fields.append('department')
            emp.modified_by = actor_name
            emp.save(update_fields=update_fields)
            promo.applied_at = now
            promo.modified_by = actor_name
            promo.save(update_fields=['applied_at', 'modified_by', 'updated_at'])
            applied += 1
    return applied


def employee_position_as_of(employee: Employee, as_of_date: date) -> dict:
    """Compute designation/department as of a historical date without relying on mutable current fields alone."""
    promos = list(
        EmployeePromotion.objects.filter(employee=employee)
        .select_related('previous_designation', 'new_designation', 'previous_department', 'new_department')
        .order_by('effective_date', 'pk')
    )
    if not promos:
        return {'designation_id': employee.designation_id, 'department_id': employee.department_id}

    # Establish initial baseline before first promotion if any future/applied promotion altered it
    desig_id = promos[0].previous_designation_id if promos[0].applied_at else employee.designation_id
    dept_id = promos[0].previous_department_id if promos[0].applied_at else employee.department_id
    for p in promos:
        if p.effective_date <= as_of_date:
            if p.new_designation_id is not None:
                desig_id = p.new_designation_id
            if p.new_department_id is not None:
                dept_id = p.new_department_id
    return {'designation_id': desig_id, 'department_id': dept_id}


# ---------------- Notices ----------------


def visible_notices_for_user(user, as_of: date | None = None):
    today = as_of or timezone.localdate()
    qs = Notice.objects.filter(is_active=True, publish_date__lte=today).filter(
        Q(expiry_date__isnull=True) | Q(expiry_date__gte=today),
    )
    if user.is_superuser or user.has_perm('hrm.publish_notice'):
        return qs
    emp = Employee.objects.filter(user_id=user.pk).first()
    if emp is None:
        return Notice.objects.none()
    dept_q = Q(target_department__isnull=True)
    if emp.department_id:
        dept_q |= Q(target_department_id=emp.department_id)
    wh_q = Q(target_warehouse__isnull=True)
    if emp.warehouse_id:
        wh_q |= Q(target_warehouse_id=emp.warehouse_id)
    return qs.filter(dept_q & wh_q)


# ---------------- Working calendar ----------------


def resolve_global_weekly_off(as_of: date) -> list[int]:
    cal = WorkingCalendar.objects.filter(effective_from__lte=as_of).order_by('-effective_from', '-pk').first()
    return list(cal.weekly_off_days) if cal else [4, 5]


def holiday_dates_for_employee(employee: Employee, start: date, end: date) -> set[date]:
    q = Q(warehouse__isnull=True)
    if employee.warehouse_id:
        q |= Q(warehouse_id=employee.warehouse_id)
    return set(Holiday.objects.filter(q, date__range=(start, end)).values_list('date', flat=True))


def is_working_day(employee: Employee, day: date, holidays: set[date] | None = None) -> bool:
    """Precedence: employee schedule > branch holiday > global WorkingCalendar default."""
    assignment = (
        EmployeeScheduleAssignment.objects.filter(employee=employee, effective_from__lte=day)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=day))
        .select_related('schedule')
        .order_by('-effective_from', '-pk')
        .first()
    )
    if assignment:
        return day.weekday() in assignment.schedule.working_days
    if holidays is None:
        holidays = holiday_dates_for_employee(employee, day, day)
    if day in holidays:
        return False
    return day.weekday() not in resolve_global_weekly_off(day)


def build_calendar_month(month_first_day: date, employee: Employee | None = None) -> dict:
    days_in_month = monthrange(month_first_day.year, month_first_day.month)[1]
    end = month_first_day.replace(day=days_in_month)
    hol_qs = Holiday.objects.filter(date__range=(month_first_day, end))
    if employee and employee.warehouse_id:
        hol_qs = hol_qs.filter(Q(warehouse__isnull=True) | Q(warehouse_id=employee.warehouse_id))
    elif not employee:
        hol_qs = hol_qs.filter(warehouse__isnull=True)
    hol_map = defaultdict(list)
    for h in hol_qs:
        hol_map[h.date].append(h.name)

    leaves = {}
    assignments = []
    if employee:
        for lv in LeaveRequest.objects.filter(
            employee=employee, status=LeaveRequest.Status.APPROVED, start_date__lte=end, end_date__gte=month_first_day,
        ).select_related('leave_type'):
            cur = max(lv.start_date, month_first_day)
            last = min(lv.end_date, end)
            while cur <= last:
                leaves[cur] = lv.leave_type.name
                cur += timedelta(days=1)
        assignments = list(
            EmployeeScheduleAssignment.objects.filter(employee=employee, effective_from__lte=end)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=month_first_day))
            .select_related('schedule')
        )

    rows = []
    for offset in range(days_in_month):
        d = month_first_day + timedelta(days=offset)
        sched = next((a.schedule for a in assignments if a.effective_from <= d and (a.effective_to is None or a.effective_to >= d)), None)
        if sched:
            working = d.weekday() in sched.working_days
        else:
            working = d.weekday() not in resolve_global_weekly_off(d)
        is_holiday = d in hol_map
        rows.append({
            'date': d.isoformat(),
            'weekday': d.weekday(),
            'is_working_day': working and not is_holiday,
            'is_holiday': is_holiday,
            'holiday_names': hol_map[d],
            'leave': leaves.get(d),
            'schedule_code': sched.code if sched else None,
        })
    return {'month': month_first_day.strftime('%Y-%m'), 'days': rows}


# ---------------- Device attendance import ----------------


def _parse_ts(raw: str, date_format: str, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name or settings.TIME_ZONE)
    raw = (raw or '').strip()
    dt = None
    for fmt in [date_format, '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S', '%d/%m/%Y %H:%M:%S']:
        if not fmt:
            continue
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        dt = datetime.fromisoformat(raw)
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(ZoneInfo(settings.TIME_ZONE))


def _resolve_shift_work_date(employee: Employee, local_ts: datetime, punch_kind: str) -> date:
    """Resolve punch timestamp to shift start date (handling overnight 22:00-06:00 checkout next day)."""
    cal_date = local_ts.date()
    prev_date = cal_date - timedelta(days=1)

    prev_assign = (
        EmployeeScheduleAssignment.objects.filter(employee=employee, effective_from__lte=prev_date)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=prev_date))
        .select_related('schedule')
        .order_by('-effective_from', '-pk')
        .first()
    )
    if prev_assign:
        sched = prev_assign.schedule
        if sched.is_overnight_shift:
            window_end_minutes = sched.checkout_window_end_minutes or 0
            shift_start = datetime.combine(prev_date, sched.start_time, tzinfo=local_ts.tzinfo)
            shift_end = datetime.combine(cal_date, sched.end_time, tzinfo=local_ts.tzinfo) + timedelta(minutes=window_end_minutes)
            # If punch falls after midnight (or before next day's window end) and isn't clearly next shift's check-in
            if shift_start <= local_ts <= shift_end:
                if punch_kind == 'out' or local_ts.time() <= (shift_end.time() if shift_end.date() == cal_date else time(23, 59)):
                    if local_ts.date() == cal_date:
                        return prev_date
    return cal_date


def preview_attendance_import(actor, *, rows: list[dict] | None = None, csv_text: str = '',
                              date_format: str = '%Y-%m-%d %H:%M:%S', tz_name: str = '') -> AttendanceImportBatch:
    if not has_role_permissions(actor, ('hrm.import_attendance',)):
        raise PermissionDenied('Permission hrm.import_attendance is required.')

    parsed_rows = list(rows or [])
    if csv_text and not parsed_rows:
        reader = csv.DictReader(StringIO(csv_text.strip()))
        for r in reader:
            parsed_rows.append({
                'device_employee_id': r.get('device_employee_id') or r.get('device_id') or r.get('employee_id') or '',
                'timestamp': r.get('timestamp') or r.get('punch_time') or '',
                'punch_kind': r.get('punch_kind') or '',
            })

    with transaction.atomic():
        username = actor.get_username()
        batch = AttendanceImportBatch.objects.create(
            date_format=date_format or '%Y-%m-%d %H:%M:%S',
            timezone=tz_name or settings.TIME_ZONE,
            status=AttendanceImportBatch.Status.PREVIEWED,
            created_by=actor,
            create_by=username,
            modified_by=username,
        )
        existing_applied_fps = set(
            AttendanceImportRow.objects.filter(status=AttendanceImportRow.Status.APPLIED).values_list('fingerprint', flat=True)
        )
        emp_by_device = defaultdict(list)
        for emp in Employee.objects.exclude(device_id='').all():
            emp_by_device[emp.device_id].append(emp)

        for item in parsed_rows:
            dev_id = str(item.get('device_employee_id') or '').strip()
            raw_ts = str(item.get('timestamp') or '').strip()
            kind = str(item.get('punch_kind') or '').strip().lower()
            try:
                ts_local = _parse_ts(raw_ts, batch.date_format, batch.timezone)
            except Exception:
                AttendanceImportRow.objects.create(
                    batch=batch, device_employee_id=dev_id, raw_timestamp=raw_ts,
                    punch_kind=kind, status=AttendanceImportRow.Status.AMBIGUOUS,
                    fingerprint=sha256(f'invalid|{dev_id}|{raw_ts}'.encode()).hexdigest(),
                    notes='Invalid timestamp format',
                    create_by=username, modified_by=username,
                )
                continue

            fp = sha256(f'{dev_id}|{ts_local.isoformat()}'.encode()).hexdigest()
            matches = emp_by_device.get(dev_id, [])
            if not matches:
                AttendanceImportRow.objects.create(
                    batch=batch, device_employee_id=dev_id, raw_timestamp=raw_ts, parsed_ts=ts_local,
                    punch_kind=kind, status=AttendanceImportRow.Status.UNMATCHED, fingerprint=fp,
                    notes='No active employee matches device_id.',
                    create_by=username, modified_by=username,
                )
                continue
            if len(matches) > 1:
                AttendanceImportRow.objects.create(
                    batch=batch, device_employee_id=dev_id, raw_timestamp=raw_ts, parsed_ts=ts_local,
                    punch_kind=kind, status=AttendanceImportRow.Status.AMBIGUOUS, fingerprint=fp,
                    notes='Multiple employees share device_id.',
                    create_by=username, modified_by=username,
                )
                continue

            emp = matches[0]
            work_date = _resolve_shift_work_date(emp, ts_local, kind)
            if fp in existing_applied_fps:
                AttendanceImportRow.objects.create(
                    batch=batch, device_employee_id=dev_id, raw_timestamp=raw_ts, parsed_ts=ts_local,
                    punch_kind=kind, employee=emp, work_date=work_date,
                    status=AttendanceImportRow.Status.DUPLICATE, fingerprint=fp,
                    notes='Already imported previously.',
                    create_by=username, modified_by=username,
                )
                continue

            AttendanceImportRow.objects.create(
                batch=batch, device_employee_id=dev_id, raw_timestamp=raw_ts, parsed_ts=ts_local,
                punch_kind=kind, employee=emp, work_date=work_date,
                status=AttendanceImportRow.Status.MATCHED, fingerprint=fp,
                create_by=username, modified_by=username,
            )
            existing_applied_fps.add(fp)
        return batch


def commit_attendance_import(actor, batch_id: int, *, reviewed_dates: list[str] | None = None, resolve_conflicts: bool = False) -> dict:
    if not has_role_permissions(actor, ('hrm.import_attendance',)):
        raise PermissionDenied('Permission hrm.import_attendance is required.')
    allowed_dates = {date.fromisoformat(d) for d in reviewed_dates} if reviewed_dates else None
    username = actor.get_username()
    with transaction.atomic():
        batch = AttendanceImportBatch.objects.select_for_update().filter(pk=batch_id).first()
        if batch is None:
            raise NotFound('Attendance import batch not found.')
        rows = list(
            batch.rows.select_for_update()
            .filter(status=AttendanceImportRow.Status.MATCHED, employee__isnull=False, work_date__isnull=False)
            .select_related('employee')
            .order_by('employee_id', 'work_date', 'parsed_ts')
        )
        if allowed_dates is not None:
            rows = [r for r in rows if r.work_date in allowed_dates]

        grouped = defaultdict(list)
        for r in rows:
            grouped[(r.employee_id, r.work_date)].append(r)

        applied_count = 0
        for (emp_id, work_date), punch_rows in sorted(grouped.items()):
            punches = sorted(r.parsed_ts for r in punch_rows)
            existing = Attendance.objects.select_for_update().filter(employee_id=emp_id, work_date=work_date).first()
            if existing and not resolve_conflicts and '[manual]' in (existing.notes or ''):
                # Preserve manual edits unless supervisor explicitly resolves conflict
                continue

            new_in = punches[0]
            new_out = punches[-1] if len(punches) > 1 or punch_rows[-1].punch_kind == 'out' else None
            if existing:
                # Re-import merge keeping earliest check-in / latest check-out for the shift start date
                if existing.check_in:
                    new_in = min(existing.check_in, new_in)
                candidates_out = [p for p in [existing.check_out, new_out] if p is not None]
                new_out = max(candidates_out) if candidates_out else None
                if new_out and new_out == new_in and len(punches) == 1 and not existing.check_out:
                    new_out = existing.check_out
                existing.check_in = new_in
                existing.check_out = new_out
                existing.status_id = EmployeeAttendanceStatus.Code.PRESENT
                existing.modified_by = username
                existing.save(update_fields=['check_in', 'check_out', 'status', 'modified_by', 'updated_at'])
            else:
                # Also look back at any earlier applied punch fingerprints on same (employee, work_date)
                Attendance.objects.create(
                    employee_id=emp_id,
                    work_date=work_date,
                    check_in=new_in,
                    check_out=new_out if new_out and new_out >= new_in else None,
                    status_id=EmployeeAttendanceStatus.Code.PRESENT,
                    notes='Imported from attendance device',
                    create_by=username,
                    modified_by=username,
                )
            for r in punch_rows:
                r.status = AttendanceImportRow.Status.APPLIED
                r.modified_by = username
                r.save(update_fields=['status', 'modified_by', 'updated_at'])
            applied_count += 1

        batch.status = AttendanceImportBatch.Status.COMMITTED
        batch.committed_at = timezone.now()
        batch.modified_by = username
        batch.save(update_fields=['status', 'committed_at', 'modified_by', 'updated_at'])
        return {'batch_id': batch.pk, 'attendance_rows': applied_count}


# ---------------- HR-03: Salary advances, increments, manual payroll vouchers ----------------


def request_advance(actor, *, employee_id: int, requested_amount, installment_count: int,
                    repayment_start_month: date, currency: str = 'BDT', reason: str = '') -> SalaryAdvance:
    amt = q_money(requested_amount)
    if amt <= ZERO:
        raise ValidationError({'requested_amount': 'Amount must be positive.'})
    if installment_count < 1:
        raise ValidationError({'installment_count': 'Installment count must be at least 1.'})
    start_m = repayment_start_month.replace(day=1)
    with transaction.atomic():
        emp = Employee.objects.filter(pk=employee_id).first()
        if emp is None:
            raise NotFound('Employee not found.')
        username = actor.get_username()
        return SalaryAdvance.objects.create(
            employee=emp, currency_id=currency, requested_amount=amt,
            installment_count=installment_count, repayment_start_month=start_m,
            reason=reason or '', status=SalaryAdvance.Status.REQUESTED,
            create_by=username, modified_by=username,
        )


def approve_advance(actor, advance_id: int, *, approved_amount=None) -> SalaryAdvance:
    if not has_role_permissions(actor, ('hrm.approve_advance',)):
        raise PermissionDenied('Permission hrm.approve_advance is required.')
    with transaction.atomic():
        adv = SalaryAdvance.objects.select_for_update().filter(pk=advance_id).first()
        if adv is None:
            raise NotFound('Salary advance not found.')
        if adv.status != SalaryAdvance.Status.REQUESTED:
            raise ValidationError({'status': 'Only requested salary advances can be approved.'})
        amt = q_money(approved_amount if approved_amount is not None else adv.requested_amount)
        if amt <= ZERO or amt > adv.requested_amount:
            raise ValidationError({'approved_amount': 'Approved amount must be > 0 and <= requested amount.'})
        adv.approved_amount = amt
        adv.status = SalaryAdvance.Status.APPROVED
        adv.approved_by = actor
        adv.approved_at = timezone.now()
        adv.modified_by = actor.get_username()
        adv.save(update_fields=['approved_amount', 'status', 'approved_by', 'approved_at', 'modified_by', 'updated_at'])
        return adv


def reject_advance(actor, advance_id: int) -> SalaryAdvance:
    if not has_role_permissions(actor, ('hrm.approve_advance',)):
        raise PermissionDenied('Permission hrm.approve_advance is required.')
    with transaction.atomic():
        adv = SalaryAdvance.objects.select_for_update().filter(pk=advance_id).first()
        if adv is None:
            raise NotFound('Salary advance not found.')
        if adv.status != SalaryAdvance.Status.REQUESTED:
            raise ValidationError({'status': 'Only requested salary advances can be rejected.'})
        adv.status = SalaryAdvance.Status.REJECTED
        adv.approved_by = actor
        adv.approved_at = timezone.now()
        adv.modified_by = actor.get_username()
        adv.save(update_fields=['status', 'approved_by', 'approved_at', 'modified_by', 'updated_at'])
        return adv


def mark_advance_paid_external(actor, advance_id: int, external_reference: str) -> SalaryAdvance:
    if not has_role_permissions(actor, ('hrm.disburse_advance',)):
        raise PermissionDenied('Permission hrm.disburse_advance is required.')
    with transaction.atomic():
        adv = SalaryAdvance.objects.select_for_update().filter(pk=advance_id).first()
        if adv is None:
            raise NotFound('Salary advance not found.')
        adv.external_paid_marker = True
        adv.external_reference = (external_reference or '').strip()
        adv.modified_by = actor.get_username()
        adv.save(update_fields=['external_paid_marker', 'external_reference', 'modified_by', 'updated_at'])
        return adv


def _add_months(month_first: date, offset: int) -> date:
    total_months = (month_first.year * 12 + (month_first.month - 1)) + offset
    return date(total_months // 12, (total_months % 12) + 1, 1)


def disburse_advance(actor, advance_id: int, account_id: int, request_key) -> tuple[SalaryAdvance, bool]:
    if not has_role_permissions(actor, ('hrm.disburse_advance',)):
        raise PermissionDenied('Permission hrm.disburse_advance is required.')

    def _do():
        with transaction.atomic():
            adv = SalaryAdvance.objects.select_for_update().select_related('employee').filter(pk=advance_id).first()
            if adv is None:
                raise NotFound('Salary advance not found.')
            if adv.status == SalaryAdvance.Status.DISBURSED:
                return IdempotentResult('salary_advance', adv.pk, {'id': adv.pk, 'loan_id': adv.loan_id}, value=adv)
            if adv.status != SalaryAdvance.Status.APPROVED:
                raise ValidationError({'status': 'Only approved advances can be disbursed.'})
            if adv.loan_id is not None:
                raise ValidationError({'loan': 'An employee loan already exists for this advance.'})

            scope = warehouse_scope(actor)
            accounts = locked_accounts(scope, account=account_id)
            account = accounts['account']
            check_active_account(account, 'account')
            if account.currency != adv.currency_id:
                raise ValidationError({'account': f'Account currency {account.currency} does not match advance currency {adv.currency_id}.'})

            amt = q_money(adv.approved_amount or adv.requested_amount)
            today = timezone.localdate()
            username = actor.get_username()

            post_transaction(
                account, AccountTransaction.Direction.OUTFLOW, actor,
                date=today, amount=amt,
                source_kind=AccountTransaction.SourceKind.SALARY_ADVANCE,
                source_ref_id=adv.pk,
                source_reference=f'ADV-{adv.pk}',
            )

            loan = EmployeeLoan.objects.create(
                employee=adv.employee,
                currency_id=adv.currency_id,
                reference=f'ADV-{adv.pk}',
                principal=amt,
                disbursed_on=today,
                notes=adv.reason or f'Salary advance #{adv.pk}',
                create_by=username,
                modified_by=username,
            )
            n = adv.installment_count
            per_inst = (amt / n).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
            allocated = ZERO
            for i in range(n):
                inst_amt = (amt - allocated) if i == n - 1 else per_inst
                allocated += inst_amt
                LoanInstallment.objects.create(
                    loan=loan,
                    month=_add_months(adv.repayment_start_month, i),
                    amount=inst_amt,
                    create_by=username,
                    modified_by=username,
                )

            adv.status = SalaryAdvance.Status.DISBURSED
            adv.account = account
            adv.loan = loan
            adv.disbursed_by = actor
            adv.disbursed_at = timezone.now()
            adv.request_key = request_key
            adv.modified_by = username
            adv.save(update_fields=[
                'status', 'account', 'loan', 'disbursed_by', 'disbursed_at',
                'request_key', 'modified_by', 'updated_at',
            ])
            log_audit_event(actor, AuditLog.Action.PAYMENT, adv, changes={'loan_id': loan.pk, 'amount': str(amt)}, request_key=request_key)
            return IdempotentResult('salary_advance', adv.pk, {'id': adv.pk, 'loan_id': loan.pk}, value=adv)

    return run_idempotent(
        request_key, f'disburse_advance:{advance_id}',
        {'advance_id': advance_id, 'account_id': account_id}, _do,
        loader=lambda r: SalaryAdvance.objects.get(pk=r.result_id),
    )


def create_manual_voucher(actor, *, employee_id: int, payment_date: date, account_id: int, amount,
                          method: str = SalaryPayment.Method.CASH, note: str = '', period_id: int | None = None,
                          reference: str = '', payslip_id: int | None = None, request_key) -> tuple[ManualPayrollVoucher, bool]:
    if not has_role_permissions(actor, ('hrm.manage_manual_payroll',)):
        raise PermissionDenied('Permission hrm.manage_manual_payroll is required.')
    amt = q_money(amount)
    if amt <= ZERO:
        raise ValidationError({'amount': 'Amount must be positive.'})

    def _do():
        with transaction.atomic():
            emp = Employee.objects.filter(pk=employee_id).first()
            if emp is None:
                raise NotFound('Employee not found.')
            target_month = payment_date.replace(day=1)
            period = PayrollPeriod.objects.filter(pk=period_id).first() if period_id else PayrollPeriod.objects.filter(month=target_month).first()

            # Duplicate-payment guard: if employee has an active/paid payslip payment for the same period and not explicitly reconciled
            if period and payslip_id is None:
                dup_paid = SalaryPayment.objects.filter(
                    payslip__employee=emp,
                    payslip__payroll_run__period=period,
                ).exclude(status=SalaryPayment.Status.FAILED).exists()
                if dup_paid:
                    raise ValidationError({
                        'payslip': 'Employee already has a payslip payment for this period. Reconcile explicitly to a payslip.',
                    })

            slip = None
            if payslip_id is not None:
                slip = Payslip.objects.filter(pk=payslip_id, employee=emp).first()
                if slip is None:
                    raise ValidationError({'payslip': 'Choose a payslip belonging to this employee.'})

            scope = warehouse_scope(actor)
            account = locked_accounts(scope, account=account_id)['account']
            check_active_account(account, 'account')
            username = actor.get_username()

            voucher = ManualPayrollVoucher.objects.create(
                employee=emp, payment_date=payment_date, account=account,
                amount=amt, method=method, note=note or '', period=period,
                reference=reference or f'MPV-{request_key}',
                payslip=slip,
                reconciled_by=actor if slip else None,
                reconciled_at=timezone.now() if slip else None,
                request_key=request_key,
                create_by=username, modified_by=username,
            )
            post_transaction(
                account, AccountTransaction.Direction.OUTFLOW, actor,
                date=payment_date, amount=amt,
                source_kind=AccountTransaction.SourceKind.SALARY_PAYMENT,
                source_ref_id=voucher.pk,
                source_reference=voucher.reference,
            )
            log_audit_event(actor, AuditLog.Action.PAYMENT, voucher, changes={'amount': str(amt), 'payslip': payslip_id}, request_key=request_key)
            return IdempotentResult('manual_payroll_voucher', voucher.pk, {'id': voucher.pk}, value=voucher)

    return run_idempotent(
        request_key, f'manual_payroll:{employee_id}',
        {'employee_id': employee_id, 'payment_date': payment_date.isoformat(), 'account_id': account_id, 'amount': str(amt), 'payslip_id': payslip_id},
        _do, loader=lambda r: ManualPayrollVoucher.objects.get(pk=r.result_id),
    )


def reconcile_voucher_to_payslip(actor, voucher_id: int, payslip_id: int) -> ManualPayrollVoucher:
    if not has_role_permissions(actor, ('hrm.manage_manual_payroll',)):
        raise PermissionDenied('Permission hrm.manage_manual_payroll is required.')
    with transaction.atomic():
        v = ManualPayrollVoucher.objects.select_for_update().filter(pk=voucher_id).first()
        if v is None:
            raise NotFound('Manual voucher not found.')
        slip = Payslip.objects.filter(pk=payslip_id, employee_id=v.employee_id).first()
        if slip is None:
            raise ValidationError({'payslip': 'Choose a payslip belonging to this employee.'})
        v.payslip = slip
        v.period = slip.payroll_run.period
        v.reconciled_by = actor
        v.reconciled_at = timezone.now()
        v.modified_by = actor.get_username()
        v.save(update_fields=['payslip', 'period', 'reconciled_by', 'reconciled_at', 'modified_by', 'updated_at'])
        return v
