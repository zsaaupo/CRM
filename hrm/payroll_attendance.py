from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError

from .leave_balances import DAY, ZERO
from .models import AttendancePolicy, EmployeeAttendanceStatus


HALF_DAY = Decimal('0.50')
ATTENDANCE = EmployeeAttendanceStatus.Code


def classify_days(employee, payable_days, calendar, threshold, result):
    approvals = {row.work_date: row for row in employee.payroll_approved_days}
    attendance = {row.work_date: row for row in employee.payroll_attendance}
    date_keys = {day.isoformat() for day in payable_days}
    for day in approvals.keys() & payable_days.keys():
        if day.isoformat() not in calendar:
            result['blockers'].append(f'Approved leave on {day} does not match the payroll calendar.')
    for leave in employee.payroll_leave:
        if not leave.has_days and any(leave.start_date <= date.fromisoformat(day) <= leave.end_date
                                     for day in date_keys & calendar.keys()):
            result['blockers'].append(f'Reconcile legacy leave request #{leave.pk} before calculating payroll.')
    flags = defaultdict(list)
    days = {}
    for day, _ in sorted(payable_days.items()):
        day_text = day.isoformat()
        shift = calendar.get(day_text)
        if shift is None:
            continue
        if shift.get('conflict'):
            result['blockers'].append(f'Reconcile overlapping work schedules on {day}.')
            continue
        row = attendance.get(day)
        leave = approvals.get(day)
        info = {'date': day_text, 'shift': shift, 'attendance_id': row.pk if row else None,
                'attendance_status': row.status_id if row else None,
                'leave_id': leave.request_id if leave else None, 'paid_leave': leave.is_paid if leave else None,
                'attended': False, 'late': False, 'early': False}
        days[day] = info
        if leave:
            continue
        if row is None or not row.check_in:
            label = 'Missing attendance' if row is None or row.status_id == ATTENDANCE.UNRECORDED else 'Attendance requires review'
            flags[label].append(day_text)
            continue
        if row.is_late is None or row.scheduled_start is None or row.scheduled_end is None:
            result['blockers'].append(f'Review and re-save the unclassified attendance record on {day}.')
            continue
        info.update(attended=True, late=row.is_late, shift=row.schedule_snapshot,
                    scheduled_start=row.scheduled_start.isoformat(), scheduled_end=row.scheduled_end.isoformat(),
                    check_in=row.check_in.isoformat(), check_out=row.check_out.isoformat() if row.check_out else None)
        if row.check_out is None or row.check_out < row.check_in:
            flags['Missing or invalid checkout'].append(day_text)
        else:
            info['early'] = row.scheduled_end - row.check_out >= timedelta(minutes=threshold)
    result['warnings'] = [{'message': message, 'dates': dates} for message, dates in flags.items()]
    return days


def attendance_policy_lines(employee, payable_days, snapshot, calendar_days, plan, result, deduction_salary):
    policy = snapshot['policy']
    calendar = snapshot['employees'].get(str(employee.pk))
    if calendar is None:
        result['blockers'].append('This employee is missing from the frozen payroll calendar.')
        return []
    working_days = sum(not shift.get('conflict') for shift in calendar.values())
    days = classify_days(employee, payable_days, calendar, policy['early_leave_threshold_minutes'], result)
    late_marks = sum(info['late'] for info in days.values())
    chargeable = max(0, late_marks - policy['monthly_late_allowance'])
    late_days = Decimal(chargeable // policy['late_marks_per_leave_day'])
    early_days = sum((HALF_DAY for info in days.values() if info['early']), ZERO)
    penalties = late_days + early_days
    uncovered_penalties, covered = {}, ZERO
    coverage = []
    day_type = policy['penalty_leave_type_id']
    for day, info in reversed(days.items()):
        if not penalties:
            break
        if not info['attended']:
            continue
        assigned = min(penalties, DAY)
        uncovered = assigned
        if day_type:
            try:
                entries, uncovered = plan.debit(employee.pk, day_type, day, assigned)
            except ValidationError as exc:
                result['blockers'].extend(exc.messages)
                break
            coverage.extend(entries)
        info.update(penalty_days=str(assigned), covered_days=str(assigned - uncovered), unpaid_penalty_days=str(uncovered))
        covered += assigned - uncovered
        if uncovered:
            uncovered_penalties[day] = uncovered
        penalties -= assigned

    paid_leave = [{'date': info['date'], 'leave_id': info['leave_id']} for info in days.values() if info['paid_leave']]
    unpaid_leave = {day: DAY for day, info in days.items() if info['paid_leave'] is False}
    absences = {day: DAY for day, info in days.items() if policy['deduct_absences'] and info['paid_leave'] is None
                and not info['attended'] and info['attendance_status'] in (None, ATTENDANCE.UNRECORDED, ATTENDANCE.ABSENT)}
    divisor = working_days if policy['deduction_divisor'] == AttendancePolicy.DeductionDivisor.WORKING else calendar_days
    deduction_lines = []
    for code, name, charges in (
        ('unpaid-leave', 'Approved unpaid leave', unpaid_leave),
        ('attendance-absence', 'Unpaid absence', absences),
        ('attendance-penalty', 'Uncovered attendance penalties', uncovered_penalties),
    ):
        inputs = []
        amount = ZERO
        for day, unpaid in sorted(charges.items()):
            salary = deduction_salary(payable_days[day], policy['deduction_basis'])
            amount += salary * unpaid / divisor if divisor else ZERO
            info = days[day]
            info['unpaid_days'] = str(unpaid)
            inputs.append({'date': info['date'], 'leave_id': info['leave_id'], 'days': str(unpaid),
                           'salary_basis': str(salary), 'divisor': divisor})
        if inputs:
            deduction_lines.append({'code': code, 'name': name, 'amount': amount, 'calculation_inputs': inputs})

    result['calculation_inputs'].update({
        'policy': 'attendance_policy',
        'attendance_policy': policy,
        'attendance_summary': {'working_days': len(days), 'late_marks': late_marks, 'late_penalty_days': str(late_days),
            'early_penalty_days': str(early_days), 'covered_days': str(covered),
            'unpaid_days': str(sum(unpaid_leave.values(), ZERO) + sum(absences.values(), ZERO) + sum(uncovered_penalties.values(), ZERO))},
        'attendance_days': list(days.values()), 'leave_coverage': coverage, 'paid_leave_days': paid_leave,
        'unpaid_leave_days': [{'date': day.isoformat(), 'leave_id': days[day]['leave_id']} for day in unpaid_leave],
    })
    return deduction_lines
