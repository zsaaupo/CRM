"""Monthly payroll calculation and atomic persistence for the HRM API."""

from calendar import monthrange
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from hashlib import sha256
import json

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Prefetch, Q
from django.utils import timezone

from audit_log.service import bulk_create_with_audit

from .attendance_policy import calendar_snapshot, dates, effective_policy, holiday_dates, period_calendar
from .leave_balances import INACTIVE_RUNS, RESERVING_RUNS, BalancePlan, lock_employees
from .models import (
    ApprovedLeaveDay, Attendance, AttendancePolicy, Employee, EmployeeAttendanceStatus, EmployeeCompensation, EmployeeLoan,
    EmployeeScheduleAssignment, LeaveRequest, LoanInstallment, PayrollAdjustment, PayrollDeductionRule, PayrollEvent, PayrollPeriod,
    PayrollRun, Payslip, PayslipLine, SalaryComponent, SalaryStructureLine,
)
from .payroll_attendance import attendance_policy_lines
from .promotions import effective_history


MONEY_PRECISION = Decimal('0.0001')
MAX_AMOUNT = Decimal('99999999999999.9999')
EARNING, DEDUCTION = SalaryComponent.Kind.EARNING, SalaryComponent.Kind.DEDUCTION
METHOD = SalaryStructureLine.CalculationMethod
ATTENDANCE = EmployeeAttendanceStatus.Code
FINALIZED = (PayrollRun.Status.APPROVED, PayrollRun.Status.PAYMENT_PROCESSING, PayrollRun.Status.PAID)
RECALCULABLE = (PayrollRun.Status.CALCULATED, PayrollRun.Status.REJECTED, PayrollRun.Status.CANCELLED)


class PayrollConflict(Exception):
    pass


class PayrollBlocked(Exception):
    def __init__(self, preview):
        self.preview = preview
        super().__init__('Resolve the payroll blockers and preview the period again.')


def overlaps(start, end):
    return Q(effective_from__lte=end) & (Q(effective_to__isnull=True) | Q(effective_to__gte=start))


def payroll_employees(start, end, currency):
    rules = SalaryStructureLine.objects.select_related('component').order_by('pk')
    deductions = PayrollDeductionRule.objects.filter(overlaps(start, start)).select_related('component').order_by('pk')
    compensation = EmployeeCompensation.objects.filter(overlaps(start, end)).select_related('salary_structure')
    compensation = compensation.prefetch_related(Prefetch('salary_structure__lines', queryset=rules),
        Prefetch('salary_structure__deduction_rules', queryset=deductions, to_attr='payroll_deductions'))
    schedules = EmployeeScheduleAssignment.objects.filter(overlaps(start, end)).select_related('schedule')
    leave = LeaveRequest.objects.filter(status=LeaveRequest.Status.APPROVED, start_date__lte=end, end_date__gte=start).annotate(
        has_days=Exists(ApprovedLeaveDay.objects.filter(request_id=OuterRef('pk'))))
    approved_days = ApprovedLeaveDay.objects.filter(work_date__range=(start, end), request__status=LeaveRequest.Status.APPROVED)
    attendance = Attendance.objects.filter(work_date__range=(start, end))
    adjustments = PayrollAdjustment.objects.filter(status=PayrollAdjustment.Status.APPROVED,
        effective_on__range=(start, end), currency_id=currency).select_related('component').order_by('pk')
    due = LoanInstallment.objects.filter(month=start, recovered_line__isnull=True)
    loans = EmployeeLoan.objects.filter(currency_id=currency, installments__in=due).distinct().prefetch_related(
        Prefetch('installments', queryset=due, to_attr='payroll_installments'))
    employed = Q(joining_date__lte=end) & (Q(leaving_date__isnull=True) | Q(leaving_date__gte=start)) & (
        Q(is_archived=False) | Q(leaving_date__isnull=False))
    corrections = adjustments.filter(source=PayrollAdjustment.Source.CORRECTION, employee_id=OuterRef('pk'))
    designations = effective_history().filter(effective_date__lte=end)
    return Employee.all_objects.filter(employed | Exists(corrections)).select_related('department').order_by('full_name', 'pk').prefetch_related(
        Prefetch('compensation_assignments', queryset=compensation, to_attr='payroll_compensation'),
        Prefetch('schedule_assignments', queryset=schedules, to_attr='payroll_schedules'),
        Prefetch('leave_requests', queryset=leave.select_related('leave_type'), to_attr='payroll_leave'),
        Prefetch('approved_leave_days', queryset=approved_days, to_attr='payroll_approved_days'),
        Prefetch('attendance_records', queryset=attendance, to_attr='payroll_attendance'),
        Prefetch('payroll_adjustments', queryset=adjustments, to_attr='approved_payroll_adjustments'),
        Prefetch('payroll_loans', queryset=loans, to_attr='due_payroll_loans'),
        Prefetch('designation_history', queryset=designations, to_attr='payroll_designations'),
    )


def round_money(value):
    return Decimal(value).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


def money(value):
    return format(round_money(value), 'f')


def salary_line_amount(rule, basic_salary):
    if rule.calculation_method == METHOD.BASIC_SALARY:
        return basic_salary
    if rule.calculation_method == METHOD.FIXED:
        return rule.fixed_amount
    return basic_salary * rule.percentage / 100


def deduction_salary(compensation, basis):
    if basis == AttendancePolicy.DeductionBasis.GROSS:
        return sum((salary_line_amount(line, compensation.basic_salary) for line in compensation.salary_structure.lines.all()
                    if line.component.type == EARNING), Decimal('0'))
    return compensation.basic_salary


def attendance_inputs(employee, payable_days, holidays, divisor, result):
    """Resolve scheduled days and approved leave using the shift start date."""
    start, end = min(payable_days), max(payable_days)
    schedules = defaultdict(list)
    leave_days = defaultdict(list)
    for assignment in employee.payroll_schedules:
        for day in dates(max(start, assignment.effective_from), min(end, assignment.effective_to or end)):
            schedules[day].append(assignment.schedule)
    for leave in employee.payroll_leave:
        for day in dates(max(start, leave.start_date), min(end, leave.end_date)):
            leave_days[day].append(leave)

    attendance = {row.work_date: row.status_id for row in employee.payroll_attendance}
    flags = defaultdict(list)
    unpaid_days = []
    paid_days = []
    deduction = Decimal('0')
    for day, compensation in sorted(payable_days.items()):
        if day in holidays:
            continue
        day_text = day.isoformat()
        leaves = leave_days[day]
        if len(leaves) > 1:
            result['blockers'].append(f'Multiple approved leave requests cover {day_text}.')
            continue
        leave = leaves[0] if leaves else None
        unpaid = leave is not None and not leave.leave_type.is_paid
        day_schedules = schedules[day]
        if len(day_schedules) != 1:
            if unpaid:
                result['blockers'].append(f'Choose one work schedule for unpaid leave on {day_text}.')
            else:
                flags['Missing or overlapping work schedule'].append(day_text)
            continue
        if day.weekday() not in day_schedules[0].working_days:
            continue
        if unpaid:
            daily_amount = compensation.basic_salary / divisor
            deduction += daily_amount
            unpaid_days.append({'date': day_text, 'leave_id': leave.pk, 'schedule_id': day_schedules[0].pk,
                                'basic_salary': str(compensation.basic_salary), 'calendar_days': divisor})
        elif leave:
            paid_days.append({'date': day_text, 'leave_id': leave.pk})
        elif attendance.get(day) in (None, ATTENDANCE.UNRECORDED):
            flags['Missing attendance'].append(day_text)
        elif attendance[day] in (ATTENDANCE.ABSENT, ATTENDANCE.ON_LEAVE):
            flags['Attendance requires review'].append(day_text)

    result['warnings'] = [{'message': message, 'dates': days} for message, days in flags.items()]
    result['calculation_inputs'].update({'paid_leave_days': paid_days, 'unpaid_leave_days': unpaid_days})
    return deduction, unpaid_days


def calculate_employee(employee, start, end, currency, holidays, divisor, snapshot=None, balance_plan=None):
    start = max(start, employee.joining_date)
    end = min(end, employee.leaving_date or end)
    assignments = [row for row in employee.payroll_compensation if start <= end and row.effective_from <= end
                   and (row.effective_to is None or row.effective_to >= start)]
    currencies = {row.salary_structure.currency_id for row in assignments}
    other_currency = currencies and currency not in currencies
    if other_currency and not employee.approved_payroll_adjustments and not employee.due_payroll_loans:
        return None
    if other_currency:
        assignments = []

    result = {'employee': employee.pk, 'employee_code': employee.employee_code, 'employee_name': employee.full_name,
              'department_name': employee.department.name if employee.department_id else '',
              'blockers': [], 'warnings': [], 'lines': [], 'gross_pay': None, 'total_deductions': None, 'net_pay': None,
              'calculation_inputs': {'payable_from': start.isoformat() if start <= end and not other_currency else None,
                                     'payable_to': end.isoformat() if start <= end and not other_currency else None,
                                     'calendar_days': divisor, 'policy': 'calendar_days_basic_unpaid_leave',
                                     'warehouse_id': employee.warehouse_id}}
    if len(currencies) > 1 and not other_currency:
        result['blockers'].append('Compensation uses more than one currency in this period.')
        return result

    payable_days = {}
    components = {}
    total_assignment_days = 0
    for compensation in assignments:
        first = max(start, compensation.effective_from)
        last = min(end, compensation.effective_to or end)
        covered = (last - first).days + 1
        total_assignment_days += covered
        for day in dates(first, last):
            payable_days[day] = compensation
        structure = compensation.salary_structure
        rules = list(structure.lines.all())
        basic_rules = [rule for rule in rules if rule.calculation_method == METHOD.BASIC_SALARY]
        if len(basic_rules) != 1 or basic_rules[0].component.type != EARNING:
            result['blockers'].append(f'{structure.name} must have exactly one basic salary earning rule.')
            continue
        for rule in rules:
            component = rule.component
            monthly = salary_line_amount(rule, compensation.basic_salary)
            line = components.setdefault(component.pk, {'code': component.code, 'name': component.name,
                'type': component.type, 'amount': Decimal('0'), 'calculation_inputs': []})
            line['amount'] += monthly * covered / divisor
            line['calculation_inputs'].append({'compensation_id': compensation.pk, 'rule_id': rule.pk,
                'structure': structure.code, 'from': first.isoformat(), 'to': last.isoformat(), 'days': covered,
                'method': rule.calculation_method, 'monthly_amount': str(monthly), 'basic_salary': str(compensation.basic_salary),
                'percentage': str(rule.percentage) if rule.percentage is not None else None, 'calendar_days': divisor})
        recurring_ids = {rule.component_id for rule in rules}
        gross_monthly = sum(salary_line_amount(rule, compensation.basic_salary) for rule in rules if rule.component.type == EARNING)
        applied = set()
        for rule in structure.payroll_deductions:
            if rule.component_id in applied | recurring_ids or rule.component.type != DEDUCTION:
                result['blockers'].append(f'Conflicting deduction rules for {rule.component.name} in {structure.name}.')
                continue
            applied.add(rule.component_id)
            monthly = rule.value if rule.method == PayrollDeductionRule.Method.FIXED else gross_monthly * rule.value / 100
            line = components.setdefault(rule.component_id, {'code': rule.component.code, 'name': rule.component.name,
                'type': DEDUCTION, 'amount': Decimal('0'), 'calculation_inputs': []})
            line['amount'] += monthly * covered / divisor
            line['calculation_inputs'].append({'deduction_rule_id': rule.pk, 'reference': rule.reference, 'method': rule.method,
                'from': first.isoformat(), 'to': last.isoformat(), 'days': covered, 'calendar_days': divisor,
                'monthly_amount': str(monthly), 'rule_value': str(rule.value), 'recurring_gross': str(gross_monthly)})

    if not other_currency and len(payable_days) < (end - start).days + 1:
        result['blockers'].append('Compensation is missing for part or all of the payable period.')
    if not payable_days and employee.due_payroll_loans:
        result['blockers'].append('A due loan installment requires payable salary in the loan currency.')
    if total_assignment_days > len(payable_days):
        result['blockers'].append('Compensation assignments overlap in the payable period.')
    if result['blockers']:
        return result

    lines = list(components.values())
    if payable_days:
        last_payable_day = max(payable_days)
        assignment = next((entry for entry in employee.payroll_designations if entry.effective_date <= last_payable_day), None)
        if assignment and assignment.designation_id:
            result['designation_name'] = assignment.designation_name
            result['calculation_inputs']['designation_change_id'] = assignment.pk
        if snapshot:
            lines.extend({'type': DEDUCTION, **line} for line in attendance_policy_lines(
                employee, payable_days, snapshot, divisor, balance_plan, result, deduction_salary))
        else:
            deduction, unpaid = attendance_inputs(employee, payable_days, holidays, divisor, result)
            if unpaid:
                lines.append({'code': 'unpaid-leave', 'name': 'Approved unpaid leave', 'type': DEDUCTION,
                              'amount': deduction, 'calculation_inputs': unpaid})
        for loan in employee.due_payroll_loans:
            for installment in loan.payroll_installments:
                lines.append({'code': f'loan-{installment.pk}', 'name': f'Loan installment {loan.reference}', 'type': DEDUCTION,
                    'amount': installment.amount, 'calculation_inputs': [{'installment_id': installment.pk,
                        'loan_id': loan.pk, 'date': installment.month.isoformat(), 'reference': loan.reference}]})
    for adjustment in employee.approved_payroll_adjustments:
        if adjustment.source != PayrollAdjustment.Source.CORRECTION and adjustment.effective_on not in payable_days:
            result['blockers'].append(f'Adjustment {adjustment.pk} is outside the employee’s payable dates.')
            continue
        if adjustment.approved_type not in (EARNING, DEDUCTION):
            result['blockers'].append(f'Adjustment {adjustment.pk} is missing its approved component type.')
            continue
        lines.append({'code': adjustment.component.code, 'name': adjustment.component.name, 'type': adjustment.approved_type,
            'amount': adjustment.amount, 'calculation_inputs': [{'adjustment_id': adjustment.pk, 'source': adjustment.source,
                'date': adjustment.effective_on.isoformat(), 'reason': adjustment.reason, 'minutes': adjustment.overtime_minutes,
                'hourly_rate': str(adjustment.hourly_rate) if adjustment.hourly_rate is not None else None,
                'multiplier': str(adjustment.multiplier) if adjustment.multiplier is not None else None,
                'correction_of': adjustment.correction_of_id}]})
    gross = sum((round_money(line['amount']) for line in lines if line['type'] == EARNING), Decimal('0'))
    deductions = sum((round_money(line['amount']) for line in lines if line['type'] == DEDUCTION), Decimal('0'))
    if deductions > gross:
        result['blockers'].append('Deductions exceed gross pay.')
    if max(gross, deductions) > MAX_AMOUNT:
        result['blockers'].append('The calculated amounts exceed the supported payroll precision.')
    for line in lines:
        line['amount'] = money(line['amount'])
    result.update(lines=lines, gross_pay=money(gross), total_deductions=money(deductions), net_pay=money(gross - deductions))
    return result


def proposed_snapshot(month):
    frozen = period_calendar(month)
    if frozen:
        return frozen
    policy = effective_policy(month)
    return calendar_snapshot(month, policy) if policy else {}


def attendance_run_conflicts(employees, month, currency):
    by_id = {employee.pk: employee for employee in employees}
    other = Payslip.objects.filter(employee_id__in=by_id).exclude(
        payroll_run__status__in=INACTIVE_RUNS).exclude(payroll_run__period__month=month, payroll_run__currency_id=currency)
    conflicts = defaultdict(list)
    settled = defaultdict(set)
    for employee_id, saved_month, state, inputs in other.values_list(
        'employee_id', 'payroll_run__period__month', 'payroll_run__status', 'calculation_inputs',
    ):
        if saved_month < month and state in FINALIZED and inputs.get('payable_from'):
            settled[employee_id].add(saved_month)
        if 'attendance_summary' not in inputs:
            continue
        if saved_month == month:
            conflicts[employee_id].append('An attendance payroll already exists for this employee and month in another currency.')
        elif saved_month > month:
            conflicts[employee_id].append(f'A later payroll exists for {saved_month:%Y-%m}. Process payroll in month order.')
        elif state in RESERVING_RUNS:
            conflicts[employee_id].append(f'Resolve the earlier payroll draft for {saved_month:%Y-%m} first.')

    first_policy = AttendancePolicy.objects.order_by('effective_from').values_list('effective_from', flat=True).first()
    if first_policy is None or month <= first_policy:
        return conflicts
    history = EmployeeCompensation.objects.filter(employee_id__in=by_id, effective_from__lt=month).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=first_policy)).order_by('employee_id', 'effective_from')
    prior_end = month - timedelta(days=1)
    for assignment in history:
        employee = by_id[assignment.employee_id]
        if conflicts.get(employee.pk):
            continue
        begin = max(assignment.effective_from, first_policy, employee.joining_date)
        end = min(assignment.effective_to or prior_end, employee.leaving_date or prior_end, prior_end)
        if begin > end:
            continue
        required = begin.replace(day=1)
        while required <= end:
            if required not in settled[employee.pk]:
                conflicts[employee.pk].append(f'Finalize the earlier payroll for {required:%Y-%m} before this period.')
                break
            required = (required.replace(day=28) + timedelta(days=4)).replace(day=1)
    return conflicts


def preview_payroll(month, currency, *, snapshot=None):
    divisor = monthrange(month.year, month.month)[1]
    end = month.replace(day=divisor)
    snapshot = proposed_snapshot(month) if snapshot is None else snapshot
    holidays = defaultdict(set) if snapshot else holiday_dates(month, end)
    employees = list(payroll_employees(month, end, currency))
    employee_ids = [employee.pk for employee in employees]
    balance_plan = BalancePlan(employee_ids, {month.year}, excluding=(month, currency)) if snapshot else None
    conflicts = attendance_run_conflicts(employees, month, currency) if snapshot else {}

    with localcontext() as context:
        context.prec = 50
        slips = []
        for employee in employees:
            result = calculate_employee(employee, month, end, currency, holidays[None] | holidays[employee.warehouse_id],
                                        divisor, snapshot, balance_plan)
            if result is not None:
                if 'attendance_summary' in result['calculation_inputs']:
                    result['blockers'].extend(conflicts.get(employee.pk, ()))
                slips.append(result)
        blockers = [] if slips else ['There are no eligible employees for this period and currency.']
        if snapshot and PayrollRun.objects.filter(period__month=month, status__in=FINALIZED, period__attendance_snapshot={}).exists():
            blockers.append('This period already has approved payroll without a frozen attendance calendar. Start the policy in a later month.')
        can_calculate = not blockers and not any(slip['blockers'] for slip in slips)
        preview = {'month': month.isoformat(), 'currency': currency, 'payslips': slips,
                   'blockers': blockers, 'can_calculate': can_calculate}
        if snapshot:
            preview['attendance_snapshot_digest'] = sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        preview['preview_token'] = sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest() if can_calculate else None
    return preview


def save_payslips(run, rows, username):
    run.payslips.all().delete()
    slips = []
    for row in rows:
        fields = {key: value for key, value in row.items() if key not in ('employee', 'lines', 'blockers')}
        slips.append(Payslip(payroll_run=run, employee_id=row['employee'], create_by=username, modified_by=username, **fields))
    bulk_create_with_audit(Payslip.objects, slips)
    lines = [PayslipLine(payslip=slip, create_by=username, modified_by=username, **line)
             for slip, row in zip(slips, rows, strict=True) for line in row['lines']]
    bulk_create_with_audit(PayslipLine.objects, lines)

    # SQLite can round high-precision decimals. Roll back the full run if a stored amount changed.
    amounts = {slip.pk: (Decimal(slip.gross_pay), Decimal(slip.total_deductions), Decimal(slip.net_pay)) for slip in slips}
    line_amounts = {line.pk: Decimal(line.amount) for line in lines}
    for pk, gross, deductions, net in run.payslips.values_list('pk', 'gross_pay', 'total_deductions', 'net_pay'):
        if amounts[pk] != (gross, deductions, net):
            raise PayrollConflict('The database could not store the calculated amounts exactly.')
    for pk, value in PayslipLine.objects.filter(payslip__payroll_run=run).values_list('pk', 'amount'):
        if line_amounts[pk] != value:
            raise PayrollConflict('The database could not store the calculated amounts exactly.')


@transaction.atomic
def save_payroll(month, currency, token, actor):
    # The first write serializes SQLite calculations and salary changes during this transaction.
    PayrollPeriod.objects.filter(month=month).update(month=F('month'))
    username = actor.get_username()
    period, _ = PayrollPeriod.objects.get_or_create(month=month, defaults={'create_by': username, 'modified_by': username})
    existing = PayrollRun.objects.select_for_update().filter(period=period, currency_id=currency).first()
    if existing and existing.status not in RECALCULABLE:
        if period.attendance_snapshot and existing.input_digest == token:
            return existing, False
        raise PayrollConflict('This payroll run is locked.')

    policy = None if period.attendance_snapshot else effective_policy(month)
    if policy or period.attendance_snapshot:
        lock_employees(Employee.all_objects.filter(joining_date__lte=period.end_date).filter(
            Q(leaving_date__isnull=True) | Q(leaving_date__gte=month)).order_by().values('pk'))
    snapshot = period.attendance_snapshot or (calendar_snapshot(month, policy) if policy else {})
    preview = preview_payroll(month, currency, snapshot=snapshot)
    if not preview['can_calculate']:
        raise PayrollBlocked(preview)
    if token != preview['preview_token']:
        raise PayrollConflict('Payroll inputs have changed. Preview this period again before calculating.')
    if existing and existing.status == PayrollRun.Status.CALCULATED and existing.input_digest == token:
        return existing, False

    if snapshot and not period.attendance_snapshot:
        period.attendance_snapshot = snapshot
        period.attendance_frozen_at = timezone.now()
        period.modified_by = username
        period.save(update_fields=['attendance_snapshot', 'attendance_frozen_at', 'modified_by', 'updated_at'])
    run = existing or PayrollRun(period=period, currency_id=currency, create_by=username)
    run.status = PayrollRun.Status.CALCULATED
    run.reviewed_at = None
    run.reviewed_by = None
    run.calculated_at = timezone.now()
    run.calculated_by = actor
    run.input_digest = token
    run.modified_by = username
    run.save()
    save_payslips(run, preview['payslips'], username)
    PayrollEvent.objects.create(run=run, actor=actor, action='recalculated' if existing else 'calculated')
    return run, existing is None
