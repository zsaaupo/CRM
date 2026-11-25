from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounting.models import AccountTransaction, FinancialAccount
from hrm.leave_policies import (
    allocate_annual_leave, compute_late_policy_deduction, expire_carry_forward, freeze_payroll_month_policy,
)
from hrm.models import (
    Attendance, Department, Designation, Employee, EmployeeCompensation, EmployeeLoan,
    EmployeePromotion, EmployeeScheduleAssignment, EmployeeStatus, EmploymentType, LatePolicy, LeaveBalance, LeavePolicy,
    LeaveRequest, LeaveType, LoanInstallment, ManualPayrollVoucher, Notice, PayrollMonthPolicy,
    PayrollPeriod, PayrollRun, Payslip, SalaryAdvance, SalaryComponent, SalaryIncrement,
    SalaryPayment, SalaryStructure, SalaryStructureLine, WorkSchedule, WorkingCalendar,
)
from hrm.payroll import PayrollConflict, preview_payroll, save_payroll
from hrm.services import review_leave_request
from hrm.workforce import (
    apply_due_promotions, approve_advance, create_manual_voucher, commit_attendance_import,
    disburse_advance, employee_position_as_of, mark_advance_paid_external,
    preview_attendance_import, reconcile_voucher_to_payslip, record_promotion, request_advance,
    visible_notices_for_user,
)
from inventory.models import Currency
from warehouse.models import WareHouse


User = get_user_model()


@override_settings(TIME_ZONE='Asia/Dhaka')
class HRMWorkforceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bdt, _ = Currency.objects.get_or_create(
            code='BDT', defaults={'name': 'Taka', 'symbol': '৳', 'exchange_rate': Decimal('1')},
        )
        cls.su = User.objects.create_superuser(username='hr_su', email='hrsu@example.com', password='pass')
        cls.u1 = User.objects.create_user(username='emp_u1', password='pass')
        cls.u2 = User.objects.create_user(username='emp_u2', password='pass')
        cls.dept_a = Department.objects.create(code='ops', name='Operations')
        cls.dept_b = Department.objects.create(code='fin', name='Finance')
        cls.desig_1 = Designation.objects.create(code='jr', name='Junior')
        cls.desig_2 = Designation.objects.create(code='sr', name='Senior')
        cls.wh_a = WareHouse.objects.create(name='WH-A', code='WHA', address='Dhaka', phone='017')
        cls.wh_b = WareHouse.objects.create(name='WH-B', code='WHB', address='Ctg', phone='018')

        cls.emp1 = Employee.objects.create(
            employee_code='EMP-101', full_name='Night Worker', device_id='DEV-01',
            department=cls.dept_a, designation=cls.desig_1, warehouse=cls.wh_a, user=cls.u1,
            joining_date=date(2025, 1, 1),
        )
        cls.emp2 = Employee.objects.create(
            employee_code='EMP-102', full_name='Finance Worker', device_id='DEV-02',
            department=cls.dept_b, designation=cls.desig_1, warehouse=cls.wh_b, user=cls.u2,
            joining_date=date(2025, 1, 1),
        )

    def test_f_hr_1_overnight_punch_reimport_and_unmatched_device(self):
        night = WorkSchedule.objects.create(
            code='night-2206', name='Night 22-06', working_days=[0, 1, 2, 3, 4],
            start_time=time(22, 0), end_time=time(6, 0), overnight=True, checkout_window_end_minutes=240,
        )
        EmployeeScheduleAssignment.objects.create(employee=self.emp1, schedule=night, effective_from=date(2026, 1, 1))

        rows = [
            {'device_employee_id': 'DEV-01', 'timestamp': '2026-11-10 22:05:00', 'punch_kind': 'in'},
            {'device_employee_id': 'DEV-01', 'timestamp': '2026-11-11 06:10:00', 'punch_kind': 'out'},
            {'device_employee_id': 'UNKNOWN-99', 'timestamp': '2026-11-10 22:00:00', 'punch_kind': 'in'},
        ]
        batch = preview_attendance_import(self.su, rows=rows)
        self.assertEqual(batch.rows.filter(status='matched').count(), 2)
        self.assertEqual(batch.rows.filter(status='unmatched').count(), 1)
        self.assertEqual(Attendance.objects.count(), 0)

        res = commit_attendance_import(self.su, batch.pk)
        self.assertEqual(res['attendance_rows'], 1)
        att = Attendance.objects.get(employee=self.emp1)
        self.assertEqual(att.work_date, date(2026, 11, 10))
        self.assertEqual(att.status_id, 'present')
        self.assertIsNotNone(att.check_in)
        self.assertIsNotNone(att.check_out)

        # Re-import same file -> duplicates ignored, still 1 attendance row
        batch2 = preview_attendance_import(self.su, rows=rows)
        self.assertEqual(batch2.rows.filter(status='duplicate').count(), 2)
        commit_attendance_import(self.su, batch2.pk)
        self.assertEqual(Attendance.objects.filter(employee=self.emp1).count(), 1)

    def test_promotions_future_not_applied_early_and_notices_audience_isolated(self):
        promo = record_promotion(
            self.su, employee_id=self.emp1.pk,
            effective_date=date(2026, 12, 1), new_designation_id=self.desig_2.pk,
            salary_increment_amount=Decimal('5000'), reason='Annual review',
        )
        # Running command before effective date must not alter employee card
        applied_early = apply_due_promotions(as_of_date=date(2026, 11, 20), actor=self.su)
        self.assertEqual(applied_early, 0)
        self.emp1.refresh_from_db()
        self.assertEqual(self.emp1.designation_id, self.desig_1.pk)

        # On effective date -> updates designation; idempotent on repeat
        applied_due = apply_due_promotions(as_of_date=date(2026, 12, 1), actor=self.su)
        self.assertEqual(applied_due, 1)
        self.emp1.refresh_from_db()
        self.assertEqual(self.emp1.designation_id, self.desig_2.pk)
        self.assertEqual(employee_position_as_of(self.emp1, date(2026, 11, 15))['designation_id'], self.desig_1.pk)
        self.assertEqual(employee_position_as_of(self.emp1, date(2026, 12, 5))['designation_id'], self.desig_2.pk)
        self.assertEqual(apply_due_promotions(as_of_date=date(2026, 12, 5), actor=self.su), 0)

        Notice.objects.create(
            title='Ops Notice', body='Ops only', target_department=self.dept_a,
            target_warehouse=self.wh_a, publish_date=date(2026, 11, 1),
        )
        Notice.objects.create(title='All Hands', body='Global', publish_date=date(2026, 11, 1))
        self.assertEqual(visible_notices_for_user(self.u1, as_of=date(2026, 11, 10)).count(), 2)
        self.assertEqual(visible_notices_for_user(self.u2, as_of=date(2026, 11, 10)).count(), 1)

    def test_f_hr_2_annual_allocation_and_march_carry_expiry(self):
        WorkingCalendar.objects.create(effective_from=date(2025, 1, 1), weekly_off_days=[4, 5])  # Fri, Sat off
        lt = LeaveType.objects.create(
            code='annual-pol', name='Annual Policy Leave', is_paid=True,
            annual_quota=Decimal('10'), carry_forward_limit=Decimal('5'),
        )
        # Seed 2025 balance with 4 unused days
        bal_2025 = LeaveBalance.objects.create(
            employee=self.emp1, leave_type=lt, year=2025,
            allocated=Decimal('10'), carried=Decimal('0'), used=Decimal('6'),
        )
        res1, c1 = allocate_annual_leave(2026, employee_ids=[self.emp1.pk], request_key=uuid.uuid4(), actor=self.su)
        res2, c2 = allocate_annual_leave(2026, employee_ids=[self.emp1.pk], request_key=uuid.uuid4(), actor=self.su)
        self.assertTrue(c1)
        self.assertEqual(LeaveBalance.objects.filter(employee=self.emp1, leave_type=lt, year=2026).count(), 1)
        bal = LeaveBalance.objects.get(employee=self.emp1, leave_type=lt, year=2026)
        self.assertEqual(bal.allocated, Decimal('10'))
        self.assertEqual(bal.carried, Decimal('4'))
        self.assertTrue(bal.prior_year_known)

        # Unknown prior year for emp2 -> recorded explicitly
        bal2 = LeaveBalance.objects.filter(employee=self.emp2, leave_type=lt, year=2026)
        allocate_annual_leave(2026, employee_ids=[self.emp2.pk], request_key=uuid.uuid4(), actor=self.su)
        b2 = bal2.get()
        self.assertFalse(b2.prior_year_known)
        self.assertEqual(b2.carried, Decimal('0'))

        # March 31, 2026 is Tuesday (working day) -> consumes 1 day of carried leave
        lr_mar = LeaveRequest.objects.create(
            employee=self.emp1, leave_type=lt, start_date=date(2026, 3, 31), end_date=date(2026, 3, 31),
        )
        review_leave_request(lr_mar.pk, self.su, LeaveRequest.Status.APPROVED)
        bal.refresh_from_db()
        self.assertEqual(bal.carry_used, Decimal('1'))
        self.assertEqual(bal.remaining_carry, Decimal('3'))
        self.assertEqual(bal.remaining_current, Decimal('10'))

        # After March 31 -> expire remaining 3 carried days; April 1 absence consumes current allocation only
        expired_n = expire_carry_forward(2026, as_of_date=date(2026, 4, 1), actor=self.su)
        self.assertEqual(expired_n, 1)
        bal.refresh_from_db()
        self.assertEqual(bal.expired, Decimal('3'))
        self.assertEqual(bal.remaining_carry, Decimal('0'))

        lr_apr = LeaveRequest.objects.create(
            employee=self.emp1, leave_type=lt, start_date=date(2026, 4, 1), end_date=date(2026, 4, 1),
        )
        review_leave_request(lr_apr.pk, self.su, LeaveRequest.Status.APPROVED)
        bal.refresh_from_db()
        self.assertEqual(bal.used, Decimal('2'))
        self.assertEqual(bal.remaining_current, Decimal('9'))

    def test_f_hr_3_month_freeze_late_policy_and_legacy_conflict(self):
        Employee.objects.filter(pk=self.emp2.pk).update(leaving_date=date(2025, 12, 31))
        WorkingCalendar.objects.create(effective_from=date(2026, 1, 1), weekly_off_days=[4, 5])
        LatePolicy.objects.create(
            effective_from=date(2026, 1, 1), default_check_in=time(9, 0), default_check_out=time(17, 0),
            grace_minutes=15, monthly_late_allowance=1, late_marks_per_leave_day=2,
            early_leave_threshold_minutes=30,
        )
        struct = SalaryStructure.objects.create(code='hr3', name='HR3 Struct', currency=self.bdt)
        basic_c = SalaryComponent.objects.create(code='basic-hr3', name='Basic HR3', type='earning')
        SalaryStructureLine.objects.create(
            salary_structure=struct, component=basic_c, calculation_method='basic_salary',
        )
        EmployeeCompensation.objects.create(
            employee=self.emp1, salary_structure=struct, basic_salary=Decimal('30000'), effective_from=date(2026, 1, 1),
        )
        sched = WorkSchedule.objects.create(
            code='day-hr3', name='Day HR3', working_days=[0, 1, 2, 3, 6],
            start_time=time(9, 0), end_time=time(17, 0), grace_minutes=15,
        )
        EmployeeScheduleAssignment.objects.create(employee=self.emp1, schedule=sched, effective_from=date(2026, 1, 1))

        # 3 late punches in November 2026 -> allowance=1 free -> 2 chargeable / ratio 2 = 1 day deduction = 1000.0000
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('Asia/Dhaka')
        from datetime import datetime
        for d in [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]:
            Attendance.objects.create(
                employee=self.emp1, work_date=d, status_id='present',
                check_in=datetime.combine(d, time(9, 30), tzinfo=tz),
                check_out=datetime.combine(d, time(17, 0), tzinfo=tz),
            )

        # Preview does NOT freeze month policy
        prev = preview_payroll(date(2026, 11, 1), 'BDT')
        self.assertTrue(prev['can_calculate'])
        self.assertFalse(PayrollMonthPolicy.objects.filter(month=date(2026, 11, 1)).exists())
        slip_prev = next(s for s in prev['payslips'] if s['employee'] == self.emp1.pk)
        self.assertEqual(slip_prev['total_deductions'], '1000.0000')

        # Save commits and freezes policy
        run, created = save_payroll(date(2026, 11, 1), 'BDT', prev['preview_token'], self.su)
        self.assertTrue(created)
        self.assertTrue(PayrollMonthPolicy.objects.filter(month=date(2026, 11, 1)).exists())

        # Changing WorkSchedule.working_days afterward leaves saved payslip unchanged
        sched.working_days = [0, 1, 2]
        sched.save(update_fields=['working_days'])
        saved_slip = Payslip.objects.get(payroll_run=run, employee=self.emp1)
        self.assertEqual(saved_slip.total_deductions, Decimal('1000.0000'))

        # Conflicting legacy finalized run for another month without PayrollMonthPolicy blocks generation
        p_dec = PayrollPeriod.objects.create(month=date(2026, 12, 1))
        legacy_run = PayrollRun.objects.create(
            period=p_dec, currency=self.bdt, calculated_by=self.su, input_digest='x' * 64,
        )
        PayrollRun.objects.filter(pk=legacy_run.pk).update(status='approved')
        Payslip.objects.create(
            payroll_run=legacy_run, employee=self.emp1, employee_code=self.emp1.employee_code,
            employee_name=self.emp1.full_name, gross_pay=Decimal('30000'), total_deductions=Decimal('0'), net_pay=Decimal('30000'),
        )
        with self.assertRaises(ValueError):
            freeze_payroll_month_policy(date(2026, 12, 1), self.su)

    def test_f_hr_4_salary_advance_loan_disbursement_and_manual_payroll_guard(self):
        acc = FinancialAccount.objects.create(
            name='HR Cash', type=FinancialAccount.Type.CASH, currency='BDT',
            opening_balance=Decimal('20000.0000'), is_active=True,
        )
        adv = request_advance(
            self.su, employee_id=self.emp1.pk, requested_amount=Decimal('3000'),
            installment_count=3, repayment_start_month=date(2026, 12, 1), reason='Medical',
        )
        approve_advance(self.su, adv.pk, approved_amount=Decimal('3000'))
        # Approved-but-undisbursed advance creates NO loan/installments
        self.assertEqual(EmployeeLoan.objects.filter(employee=self.emp1).count(), 0)

        # Mark external paid evidence does not create loan or AccountTransaction
        mark_advance_paid_external(self.su, adv.pk, 'EXT-REF-1')
        self.assertEqual(EmployeeLoan.objects.filter(employee=self.emp1).count(), 0)
        self.assertEqual(AccountTransaction.objects.count(), 0)

        # Disburse -> 1 loan, 3 installments of 1000 each, 1 AccountTransaction OUTFLOW
        rk = uuid.uuid4()
        adv_out, c1 = disburse_advance(self.su, adv.pk, acc.pk, rk)
        adv_replay, c2 = disburse_advance(self.su, adv.pk, acc.pk, rk)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(adv_out.loan_id, adv_replay.loan_id)
        self.assertEqual(EmployeeLoan.objects.filter(employee=self.emp1).count(), 1)
        self.assertEqual(
            list(LoanInstallment.objects.filter(loan=adv_out.loan).order_by('month').values_list('amount', flat=True)),
            [Decimal('1000.0000'), Decimal('1000.0000'), Decimal('1000.0000')],
        )
        self.assertEqual(AccountTransaction.objects.filter(source_kind='salary_advance', source_ref_id=adv.pk).count(), 1)

        # Compensation increment via serializer auto-creates SalaryIncrement
        struct = SalaryStructure.objects.create(code='inc-st', name='Inc Struct', currency=self.bdt)
        bc = SalaryComponent.objects.create(code='inc-bc', name='Basic Inc', type='earning')
        SalaryStructureLine.objects.create(salary_structure=struct, component=bc, calculation_method='basic_salary')
        EmployeeCompensation.objects.create(
            employee=self.emp1, salary_structure=struct, basic_salary=Decimal('20000'),
            effective_from=date(2026, 1, 1), effective_to=date(2026, 10, 31),
        )
        client = APIClient()
        client.force_authenticate(user=self.su)
        res = client.post(
            reverse('hrm:employee-compensation-list'),
            {'employee': self.emp1.pk, 'salary_structure': struct.pk, 'basic_salary': '25000.0000', 'effective_from': '2026-11-01'},
            format='json',
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(SalaryIncrement.objects.filter(employee=self.emp1, new_amount=Decimal('25000')).count(), 1)

        # Manual payroll voucher blocked when payslip payment exists for period unless reconciled
        p_jan = PayrollPeriod.objects.create(month=date(2027, 1, 1))
        run_jan = PayrollRun.objects.create(period=p_jan, currency=self.bdt, calculated_by=self.su, input_digest='y' * 64)
        PayrollRun.objects.filter(pk=run_jan.pk).update(status='approved')
        slip_jan = Payslip.objects.create(
            payroll_run=run_jan, employee=self.emp1, employee_code=self.emp1.employee_code,
            employee_name=self.emp1.full_name, gross_pay=Decimal('25000'), total_deductions=Decimal('0'), net_pay=Decimal('25000'),
        )
        SalaryPayment.objects.create(
            payslip=slip_jan, amount=Decimal('25000'), method='cash', request_key=uuid.uuid4(),
        )
        with self.assertRaises(ValidationError):
            create_manual_voucher(
                self.su, employee_id=self.emp1.pk, payment_date=date(2027, 1, 31),
                account_id=acc.pk, amount=Decimal('5000'), period_id=p_jan.pk, request_key=uuid.uuid4(),
            )
        # Reconciled voucher succeeds and posts AccountTransaction OUTFLOW salary_payment
        v, _ = create_manual_voucher(
            self.su, employee_id=self.emp1.pk, payment_date=date(2027, 1, 31),
            account_id=acc.pk, amount=Decimal('5000'), period_id=p_jan.pk, payslip_id=slip_jan.pk, request_key=uuid.uuid4(),
        )
        self.assertEqual(v.payslip_id, slip_jan.pk)
        self.assertEqual(AccountTransaction.objects.filter(source_kind='salary_payment', source_ref_id=v.pk).count(), 1)


class LeaveConcurrencyTests(TransactionTestCase):
    def test_two_concurrent_approvals_cannot_overspend_balance(self):
        EmploymentType.objects.get_or_create(code='full-time', defaults={'name': 'Full-time'})
        EmployeeStatus.objects.get_or_create(code='active', defaults={'name': 'Active'})
        u_app = User.objects.create_user(username='conc_app')
        u_rev1 = User.objects.create_user(username='conc_rev1', is_superuser=True)
        u_rev2 = User.objects.create_user(username='conc_rev2', is_superuser=True)
        emp = Employee.objects.create(employee_code='CONC-1', full_name='Conc Emp', user=u_app)
        lt = LeaveType.objects.create(code='cl-conc', name='Annual Conc', is_paid=True, annual_quota=Decimal('3'))
        WorkingCalendar.objects.create(effective_from=date(2026, 1, 1), weekly_off_days=[4, 5])
        allocate_annual_leave(2026, employee_ids=[emp.pk], request_key=uuid.uuid4(), actor=u_rev1)

        # Two separate 2-day requests when only 3 days available -> at most 1 succeeds
        lr1 = LeaveRequest.objects.create(
            employee=emp, leave_type=lt, start_date=date(2026, 5, 3), end_date=date(2026, 5, 4),
        )
        lr2 = LeaveRequest.objects.create(
            employee=emp, leave_type=lt, start_date=date(2026, 5, 10), end_date=date(2026, 5, 11),
        )

        def _approve(req_id, reviewer):
            close_old_connections()
            try:
                review_leave_request(req_id, reviewer, LeaveRequest.Status.APPROVED)
                return 'ok'
            except Exception as exc:
                return f'{type(exc).__name__}:{exc}'

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_approve, lr1.pk, u_rev1)
            f2 = pool.submit(_approve, lr2.pk, u_rev2)
            outcomes = [f1.result(), f2.result()]

        self.assertEqual(outcomes.count('ok'), 1, f'outcomes={outcomes}')
        bal = LeaveBalance.objects.get(employee=emp, leave_type=lt, year=2026)
        self.assertLessEqual(bal.used, Decimal('3'))
