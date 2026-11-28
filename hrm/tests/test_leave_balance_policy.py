from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from hrm.leave_balances import BalancePlan, allocate_leave_balances, reconcile_approved_leave
from hrm.models import (
    ApprovedLeaveDay, Attendance, AttendancePolicy, Employee, EmployeeCompensation, EmployeeScheduleAssignment,
    Holiday, LeaveBalance, LeaveBalanceMovement, LeaveRequest, LeaveType, PayrollPeriod, PayrollRun,
    SalaryComponent, SalaryStructure, SalaryStructureLine, WorkSchedule,
)
from hrm.payroll import preview_payroll, save_payroll
from hrm.payroll_reports import payslip_document
from hrm.payroll_workflow import transition_run
from hrm.services import review_leave_request


@override_settings(TIME_ZONE='Asia/Dhaka')
class LeaveBalancePolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.reviewer = users.create_user(username='balance-reviewer')
        cls.user = users.create_user(username='balance-employee')
        cls.employee = Employee.objects.create(employee_code='POL-01', full_name='Policy Employee',
                                               joining_date=date(2025, 1, 1), user=cls.user)
        cls.paid = LeaveType.objects.create(code='annual-test', name='Annual leave', is_paid=True,
                                            annual_quota_days=20, carry_forward_limit_days=5)
        cls.unpaid = LeaveType.objects.create(code='unpaid-test', name='Unpaid leave', is_paid=False)
        cls.policy = AttendancePolicy.objects.create(effective_from=date(2025, 1, 1))

    def apply(self, start, end=None, leave_type=None):
        return LeaveRequest.objects.create(employee=self.employee, leave_type=leave_type or self.paid,
                                           start_date=start, end_date=end or start)

    def approve(self, leave):
        return review_leave_request(leave.pk, self.reviewer, LeaveRequest.Status.APPROVED)

    def grant(self, user, *codenames):
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='hrm', codename__in=codenames))

    def punch(self, day, hours, minutes=0):
        return timezone.make_aware(datetime.combine(day, time(hours, minutes)))

    def test_allocation_idempotency_and_march_31_carry(self):
        with patch('hrm.leave_balances.timezone.localdate', return_value=date(2026, 1, 1)):
            call_command('allocate_leave_balances', year=2025, verbosity=0)
            prior = LeaveBalance.objects.get(year=2025)
            LeaveBalance.objects.filter(pk=prior.pk).update(annual_used_days=12, carried_days=5)
            self.assertEqual(allocate_leave_balances(2026, 'scheduler')['carry_pending'], 1)
            self.assertEqual(allocate_leave_balances(2026, 'admin', finalize_carry=True)['carry_finalized'], 1)
            LeaveType.objects.filter(pk=self.paid.pk).update(annual_quota_days=22)
            self.assertEqual(allocate_leave_balances(2026, 'scheduler')['allocated'], 0)
        request = self.apply(date(2026, 3, 31), date(2026, 4, 1))
        self.approve(request)
        self.approve(request)
        self.assertEqual(list(request.approved_days.values_list('work_date', flat=True)), [date(2026, 3, 31), date(2026, 4, 1)])
        self.assertEqual(list(request.leavebalancemovement_set.values_list('bucket', flat=True)), ['carry', 'annual'])
        row = LeaveBalance.objects.get(year=2026)
        self.assertEqual((row.annual_quota_days, row.carried_days, row.carry_used_days, row.annual_used_days),
                         (Decimal('20'), Decimal('5'), Decimal('1'), Decimal('1')))
        self.grant(self.user, 'view_leavebalance')
        client = APIClient()
        client.force_login(self.user)
        listing = reverse('hrm:leave-balance-list')
        self.assertEqual(client.get(listing, {'as_of': '2026-03-31'}).data['results'][0]['available_days'], '23.00')
        self.assertEqual(client.get(listing, {'as_of': '2026-04-01'}).data['results'][0]['available_days'], '19.00')
        self.assertEqual(client.post(reverse('hrm:allocate-leave-balances'), {'year': 2026}).status_code, 403)

    def test_cross_year_approval_order_overlap_and_atomic_balances(self):
        with patch('hrm.leave_balances.timezone.localdate', return_value=date(2026, 1, 1)):
            allocate_leave_balances(2025, 'scheduler')
            allocate_leave_balances(2026, 'scheduler', finalize_carry=True)
        Holiday.objects.create(name='New year', date=date(2026, 1, 1))
        first = self.apply(date(2025, 12, 31), date(2026, 1, 4))
        second = self.apply(date(2026, 1, 5))
        with self.assertRaisesMessage(ValidationError, 'earlier pending requests'):
            self.approve(second)
        self.approve(first)
        self.assertEqual(list(first.approved_days.values_list('work_date', flat=True)), [date(2025, 12, 31), date(2026, 1, 2)])
        self.assertEqual(first.leavebalancemovement_set.count(), 2)
        with self.assertRaisesMessage(ValidationError, 'overlaps approved leave'):
            self.approve(self.apply(date(2026, 1, 2), leave_type=self.unpaid))
        LeaveBalance.objects.filter(year=2026).update(annual_quota_days=0, carried_days=1)
        with self.assertRaisesMessage(ValidationError, 'Insufficient paid leave'):
            self.approve(second)
        second.refresh_from_db()
        self.assertEqual(second.status, LeaveRequest.Status.PENDING)
        self.assertEqual(LeaveBalanceMovement.objects.count(), 2)

    def test_reconciles_legacy_approvals_exactly_once(self):
        allocate_leave_balances(2026, 'scheduler')
        leave = LeaveRequest.objects.create(employee=self.employee, leave_type=self.paid, start_date=date(2026, 2, 2),
            end_date=date(2026, 2, 2), status='approved', reviewed_by=self.reviewer, reviewed_at=timezone.now())
        self.assertEqual(reconcile_approved_leave(2026, self.reviewer.username), 1)
        self.assertEqual(reconcile_approved_leave(2026, self.reviewer.username), 0)
        self.assertTrue(ApprovedLeaveDay.objects.get(request=leave).is_paid)
        self.assertEqual(LeaveBalance.objects.get(year=2026).annual_used_days, Decimal('1'))

    def test_attendance_csv_and_manual_share_classification_and_shift_history(self):
        self.grant(self.reviewer, 'add_attendance', 'view_attendancepolicy', 'add_attendancepolicy')
        client = APIClient()
        client.force_login(self.reviewer)
        page = client.get(reverse('hrm_pages:add-attendance-policy'))
        self.assertContains(page, 'Loss-of-pay divisor')
        bad_policy = client.post(reverse('hrm:attendance-policy-list'), {'effective_from': '2026-02-02'}, format='json')
        self.assertEqual(bad_policy.status_code, 400)
        fresh = client.post(reverse('hrm:attendance-policy-list'), {'effective_from': '2026-03-01'}, format='json')
        self.assertEqual(fresh.status_code, 201, fresh.data)
        self.assertEqual(fresh.data['attendance_policy']['grace_minutes'], 15)
        day = date(2026, 2, 2)
        manual = client.post(reverse('hrm:attendance-list'), {'employee': self.employee.pk, 'work_date': day.isoformat(),
                             'status': 'present', 'check_in': '2026-02-02T09:15:00'}, format='json')
        self.assertEqual(manual.status_code, 201)
        self.assertFalse(manual.data['attendance']['is_late'])
        upload = SimpleUploadedFile('attendance.csv', b'employee_code,work_date,status,check_in\nPOL-01,2026-02-03,present,2026-02-03 09:16:00\n')
        self.assertEqual(client.post(reverse('hrm:attendance-import'), {'file': upload}, format='multipart').status_code, 201)
        self.assertTrue(Attendance.objects.get(work_date=date(2026, 2, 3)).is_late)
        schedule = WorkSchedule.objects.create(code='policy-night', name='Night', start_time=time(22), end_time=time(6),
                                               grace_minutes=5, working_days=[0, 1, 2, 3, 4])
        EmployeeScheduleAssignment.objects.create(employee=self.employee, schedule=schedule, effective_from=date(2026, 2, 4))
        night = Attendance.objects.create(employee=self.employee, work_date=date(2026, 2, 4), status_id='present',
            check_in=self.punch(date(2026, 2, 4), 22, 6), check_out=self.punch(date(2026, 2, 5), 6))
        schedule.grace_minutes = 30
        schedule.save(update_fields=['grace_minutes'])
        night.save()
        self.assertTrue(night.is_late)
        self.assertEqual(timezone.localtime(night.scheduled_end).date(), date(2026, 2, 5))
        self.assertEqual(night.schedule_snapshot['grace'], 5)

    def test_payroll_freezes_calendar_and_reserves_then_posts_coverage(self):
        self.policy.penalty_leave_type = self.paid
        self.policy.deduction_divisor = AttendancePolicy.DeductionDivisor.WORKING
        self.policy.save()
        LeaveType.objects.filter(pk=self.paid.pk).update(annual_quota_days=1)
        allocate_leave_balances(2026, 'scheduler')
        structure = SalaryStructure.objects.create(code='policy-test', name='Monthly', currency_id='BDT')
        basic = SalaryComponent.objects.create(code='policy-basic', name='Basic salary', type='earning')
        SalaryStructureLine.objects.create(salary_structure=structure, component=basic, calculation_method='basic_salary')
        month = date(2026, 2, 1)
        EmployeeCompensation.objects.create(employee=self.employee, salary_structure=structure,
                                            basic_salary=30000, effective_from=month)
        early_preview = preview_payroll(date(2026, 3, 1), 'BDT')
        self.assertIn('Finalize the earlier payroll for 2026-02 before this period.', early_preview['payslips'][0]['blockers'])
        self.approve(self.apply(date(2026, 2, 11), leave_type=self.unpaid))
        for day_number in (2, 3, 4, 5, 6, 9, 10):
            day = month.replace(day=day_number)
            Attendance.objects.create(employee=self.employee, work_date=day, status_id='present',
                check_in=self.punch(day, 9, 16), check_out=self.punch(day, 13 if day_number == 2 else 17))
        preview = preview_payroll(month, 'BDT')
        self.assertTrue(preview['can_calculate'], preview)
        summary = preview['payslips'][0]['calculation_inputs']['attendance_summary']
        self.assertEqual(summary['late_marks'], 7)
        self.assertEqual(Decimal(summary['late_penalty_days']), Decimal('2'))
        self.assertEqual(Decimal(summary['early_penalty_days']), Decimal('0.50'))
        self.assertEqual(Decimal(summary['unpaid_days']), Decimal('2.50'))
        self.assertEqual(preview['payslips'][0]['total_deductions'], '3750.0000')
        token = preview['preview_token']
        run, created = save_payroll(month, 'BDT', token, self.reviewer)
        self.assertTrue(created)
        self.assertFalse(LeaveBalanceMovement.objects.exists())
        self.assertEqual(save_payroll(month, 'BDT', token, self.reviewer), (run, False))
        with self.assertRaisesMessage(ValidationError, 'Insufficient paid leave'):
            self.approve(self.apply(date(2026, 3, 2)))
        with self.assertRaisesMessage(ValidationError, 'active saved payslip'):
            self.approve(self.apply(date(2026, 2, 12)))
        self.policy.grace_minutes = 30
        self.policy.save()
        Holiday.objects.create(name='Holiday added later', date=month.replace(day=3))
        self.assertEqual(preview_payroll(month, 'BDT')['preview_token'], token)
        transition_run(run.pk, 'cancel', self.reviewer, 'Recalculate')
        self.assertFalse(LeaveBalanceMovement.objects.exists())
        plan = BalancePlan([self.employee.pk], {month.year})
        balance = plan.balances[self.employee.pk, self.paid.pk, month.year]
        self.assertEqual(sum(plan.available_buckets(balance, month)), Decimal('1'))
        self.assertEqual(save_payroll(month, 'BDT', token, self.reviewer), (run, False))
        transition_run(run.pk, 'review', self.reviewer)
        self.assertEqual(transition_run(run.pk, 'approve', self.reviewer).status, PayrollRun.Status.APPROVED)
        self.assertEqual(transition_run(run.pk, 'approve', self.reviewer).status, PayrollRun.Status.APPROVED)
        self.assertEqual(LeaveBalance.objects.get(year=2026).annual_used_days, Decimal('1'))
        debit = LeaveBalanceMovement.objects.get()
        self.assertEqual(debit.effective_date, month.replace(day=10))
        self.assertEqual(debit.quantity_days, Decimal('1'))
        self.assertEqual(payslip_document(debit.payslip_id)['attendance']['late_marks'], 7)
        self.assertIsNotNone(PayrollPeriod.objects.get(month=month).attendance_frozen_at)
