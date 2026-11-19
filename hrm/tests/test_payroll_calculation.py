from datetime import date, time
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from hrm.models import (
    Attendance, Employee, EmployeeCompensation, EmployeeScheduleAssignment, Holiday, LeaveRequest, LeaveType,
    LoanInstallment, PayrollAdjustment, PayrollPeriod, PayrollRun, Payslip, PayslipLine, SalaryComponent, SalaryStructure,
    SalaryStructureLine, WorkSchedule,
)
from hrm.permissions import (PAYROLL_APPROVE, PAYROLL_CALCULATE, PAYROLL_DETAIL, PAYROLL_PAYMENT_WRITE, PAYROLL_REVIEW,
                             PAYROLL_REVIEW_ADJUSTMENT, PAYROLL_VIEW)
from inventory.models import Currency


class PayrollCalculationTests(TestCase):
    month = {'month': '2026-11-01', 'currency': 'BDT'}

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='payroll-calculator')
        cls.group = Group.objects.create(name='Payroll calculators')
        cls.user.groups.add(cls.group)
        cls.employee = Employee.objects.create(employee_code='CALC-1', full_name='Payroll employee', joining_date='2026-01-01')
        cls.structure = SalaryStructure.objects.create(code='monthly', name='Monthly', currency_id='BDT')
        for code, name, method, values in [
            ('basic', 'Basic salary', 'basic_salary', {}),
            ('rent', 'House rent', 'percentage_of_basic', {'percentage': Decimal('40')}),
            ('medical', 'Medical allowance', 'fixed', {'fixed_amount': Decimal('3000')}),
        ]:
            component = SalaryComponent.objects.create(code=code, name=name, type='earning')
            SalaryStructureLine.objects.create(salary_structure=cls.structure, component=component,
                                               calculation_method=method, **values)
        cls.previous = EmployeeCompensation.objects.create(employee=cls.employee, salary_structure=cls.structure,
            basic_salary=30000, effective_from='2026-01-01', effective_to='2026-11-15')
        cls.current = EmployeeCompensation.objects.create(employee=cls.employee, salary_structure=cls.structure,
            basic_salary=36000, effective_from='2026-11-16')
        cls.schedule = WorkSchedule.objects.create(code='weekdays', name='Weekdays', start_time=time(9), end_time=time(17),
                                                   working_days=[0, 1, 2, 3, 4])
        EmployeeScheduleAssignment.objects.create(employee=cls.employee, schedule=cls.schedule, effective_from='2026-01-01')
        cls.unpaid = LeaveType.objects.create(code='unpaid', name='Unpaid leave', is_paid=False)
        cls.paid = LeaveType.objects.create(code='annual', name='Annual leave', is_paid=True)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.csrf = self.client.get(reverse('account:login')).data['csrfToken']

    def grant(self, *permissions):
        self.group.permissions.add(*Permission.objects.filter(content_type__app_label='hrm',
            codename__in=[permission.split('.')[1] for permission in permissions]))

    def add_leave(self, start, end, leave_type, status='approved'):
        return LeaveRequest.objects.create(employee=self.employee, leave_type=leave_type, start_date=start, end_date=end,
            status=status, reviewed_by=self.user if status == 'approved' else None,
            reviewed_at=timezone.now() if status == 'approved' else None)

    def post(self, action, payload):
        return self.client.post(reverse(f'hrm:payroll-run-{action}'), payload, format='json', HTTP_X_CSRFTOKEN=self.csrf)

    def preview(self):
        response = self.post('preview', self.month)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def save(self, preview):
        return self.post('calculate', {**self.month, 'preview_token': preview['preview_token']})

    def test_prorates_a_midmonth_raise_and_deducts_only_approved_unpaid_workdays(self):
        self.grant(*PAYROLL_CALCULATE)
        self.add_leave('2026-11-16', '2026-11-16', self.unpaid)
        self.add_leave('2026-11-17', '2026-11-17', self.paid)
        self.add_leave('2026-11-18', '2026-11-18', self.unpaid, 'pending')
        self.add_leave('2026-11-20', '2026-11-22', self.unpaid)
        Holiday.objects.create(date='2026-11-20', name='Company holiday')
        Attendance.objects.create(employee=self.employee, work_date='2026-11-16', status_id='absent')
        Attendance.objects.create(employee=self.employee, work_date='2026-11-19', status_id='absent')

        preview = self.preview()
        self.assertTrue(preview['can_calculate'])
        slip, = preview['payslips']
        self.assertEqual((slip['gross_pay'], slip['total_deductions'], slip['net_pay']), ('49200.0000', '1200.0000', '48000.0000'))
        self.assertEqual({line['code']: line['amount'] for line in slip['lines']},
                         {'basic': '33000.0000', 'rent': '13200.0000', 'medical': '3000.0000', 'unpaid-leave': '1200.0000'})
        self.assertEqual([item['date'] for item in slip['calculation_inputs']['unpaid_leave_days']], ['2026-11-16'])
        self.assertEqual([item['date'] for item in slip['calculation_inputs']['paid_leave_days']], ['2026-11-17'])
        self.assertIn({'message': 'Attendance requires review', 'dates': ['2026-11-19']}, slip['warnings'])
        self.assertFalse(PayrollPeriod.objects.exists())

    def test_prorates_a_join_and_configured_fixed_deduction(self):
        self.grant(*PAYROLL_CALCULATE)
        Employee.objects.filter(pk=self.employee.pk).update(joining_date=date(2026, 11, 16))
        component = SalaryComponent.objects.create(code='fixed-deduction', name='Fixed deduction', type='deduction')
        SalaryStructureLine.objects.create(salary_structure=self.structure, component=component, calculation_method='fixed',
                                           fixed_amount=3000)
        slip, = self.preview()['payslips']
        self.assertEqual((slip['gross_pay'], slip['total_deductions'], slip['net_pay']), ('26700.0000', '1500.0000', '25200.0000'))

    def test_save_snapshots_is_idempotent_and_requires_a_fresh_preview(self):
        self.grant(*PAYROLL_CALCULATE)
        preview = self.preview()
        saved = self.save(preview)
        self.assertEqual(saved.status_code, 201, saved.data)
        run_id = saved.data['id']
        original = Payslip.objects.get()
        self.assertEqual(original.gross_pay, Decimal('49200'))
        self.assertEqual(self.save(preview).status_code, 200)
        self.assertEqual(Payslip.objects.get().pk, original.pk)

        EmployeeCompensation.objects.filter(pk=self.current.pk).update(basic_salary=39000)
        self.assertEqual(self.save(preview).status_code, 409)
        self.assertEqual(Payslip.objects.get().gross_pay, Decimal('49200'))
        recalculated = self.save(self.preview())
        self.assertEqual(recalculated.status_code, 200, recalculated.data)
        self.assertEqual(recalculated.data['id'], run_id)
        self.assertEqual(PayrollPeriod.objects.count(), 1)
        self.assertEqual(PayrollRun.objects.count(), 1)
        self.assertEqual(Payslip.objects.get().gross_pay, Decimal('51300'))
        self.assertEqual(PayslipLine.objects.count(), 3)

        PayrollRun.objects.filter(pk=run_id).update(status=PayrollRun.Status.APPROVED)
        self.assertFalse(self.preview()['can_calculate'])
        self.assertEqual(self.save(preview).status_code, 409)
        self.assertEqual(Payslip.objects.get().gross_pay, Decimal('51300'))

    def test_blocked_preview_cannot_write_and_explains_missing_inputs(self):
        self.grant(*PAYROLL_CALCULATE)
        self.add_leave('2026-11-16', '2026-11-16', self.unpaid)
        EmployeeScheduleAssignment.objects.all().delete()
        preview = self.preview()
        self.assertFalse(preview['can_calculate'])
        self.assertIn('Choose one work schedule for unpaid leave on 2026-11-16.', preview['payslips'][0]['blockers'])
        response = self.post('calculate', {**self.month, 'preview_token': '0' * 64})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(PayrollPeriod.objects.exists())

        EmployeeCompensation.objects.filter(pk=self.previous.pk).delete()
        self.assertIn('Compensation is missing for part or all of the payable period.', self.preview()['payslips'][0]['blockers'])

    def test_month_and_currency_validation_and_scope(self):
        self.grant(*PAYROLL_CALCULATE)
        self.assertEqual(self.post('preview', {**self.month, 'month': '2026-11-02'}).status_code, 400)
        self.assertEqual(self.post('preview', {**self.month, 'currency': 'XXX'}).status_code, 400)
        usd, _ = Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar'})
        usd_structure = SalaryStructure.objects.create(code='monthly-usd', name='Monthly USD', currency=usd)
        EmployeeCompensation.objects.filter(pk=self.current.pk).update(salary_structure=usd_structure)
        self.assertIn('Compensation uses more than one currency in this period.', self.preview()['payslips'][0]['blockers'])

    def test_permission_gates_and_api_connected_pages(self):
        listing = reverse('hrm_pages:payroll-run-list')
        add_page = reverse('hrm_pages:add-payroll-run')
        self.assertEqual(self.client.get(listing).status_code, 403)
        self.assertEqual(self.post('preview', self.month).status_code, 403)
        self.grant(*PAYROLL_DETAIL)
        page = self.client.get(listing)
        self.assertContains(page, 'Payroll run list')
        self.assertNotContains(page, 'Calculate payroll')
        self.assertEqual(self.client.get(add_page).status_code, 403)
        self.grant(*PAYROLL_CALCULATE)
        self.assertContains(self.client.get(listing), 'Calculate payroll')
        form = self.client.get(add_page)
        self.assertContains(form, f'action="{reverse("hrm:payroll-run-calculate")}"')
        self.assertContains(form, f'data-preview-url="{reverse("hrm:payroll-run-preview")}"')
        self.assertEqual(self.client.post(reverse('hrm:payroll-run-preview'), self.month, format='json').status_code, 403)
        saved = self.save(self.preview())
        self.assertEqual(saved.status_code, 201, saved.data)
        detail_api = reverse('hrm:payroll-run-detail', args=[saved.data['id']])
        detail = reverse('hrm_pages:payroll-run-detail', args=[saved.data['id']])
        self.assertContains(self.client.get(detail), f'data-detail-url="{detail_api}"')
        self.assertEqual(self.client.get(detail_api).data['payslips'][0]['net_pay'], '49200.0000')
        run_list = self.client.get(reverse('hrm:payroll-run-list-view'), {'q': '2026-11'})
        self.assertEqual(run_list.data['count'], 1)
        self.group.permissions.clear()
        self.grant(*PAYROLL_VIEW)
        self.assertEqual(self.client.get(detail).status_code, 403)
        self.assertEqual(self.client.get(detail_api).status_code, 403)

    def request_api(self, name, payload, *args):
        return self.client.post(reverse(f'hrm:{name}', args=args), payload, format='json', HTTP_X_CSRFTOKEN=self.csrf)

    def create_input(self, key, payload):
        response = self.request_api(f'{key}-list', payload)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data[key.replace('-', '_')]

    def transition(self, run, action, **payload):
        return self.request_api(f'payroll-run-{action}', payload, run)

    def saved_and_approved(self):
        response = self.save(self.preview())
        self.assertEqual(response.status_code, 201, response.data)
        run = response.data['id']
        self.assertEqual(self.transition(run, 'review').status_code, 200)
        approved = self.transition(run, 'approve')
        self.assertEqual(approved.status_code, 200, approved.data)
        return run

    def test_review_reject_recalculate_and_approve_are_separate_permissions(self):
        self.grant(*PAYROLL_CALCULATE)
        previous = self.preview()
        saved = self.save(previous)
        run = saved.data['id']
        self.assertEqual(self.transition(run, 'review').status_code, 403)
        self.grant(*PAYROLL_REVIEW)
        self.assertEqual(self.transition(run, 'reject').status_code, 400)
        self.assertEqual(self.transition(run, 'reject', note='Check source data').data['status'], 'rejected')
        self.assertEqual(self.save(self.preview()).data['status'], 'calculated')
        self.assertEqual(self.transition(run, 'cancel', note='Wrong month').data['status'], 'cancelled')
        self.assertEqual(self.save(self.preview()).data['status'], 'calculated')
        self.assertEqual(self.transition(run, 'review').data['status'], 'reviewed')
        self.assertEqual(self.transition(run, 'approve').status_code, 403)
        self.grant(*PAYROLL_APPROVE)
        EmployeeCompensation.objects.filter(pk=self.current.pk).update(basic_salary=37000)
        self.assertEqual(self.transition(run, 'approve').status_code, 409)
        self.assertEqual(self.transition(run, 'return').data['status'], 'calculated')
        self.assertEqual(self.save(self.preview()).status_code, 200)
        self.assertEqual(self.transition(run, 'review').status_code, 200)
        self.assertEqual(self.transition(run, 'approve').data['status'], 'approved')
        self.assertEqual(self.save(previous).status_code, 409)

        detail = self.client.get(reverse('hrm:payroll-run-detail', args=[run])).data
        self.assertEqual([event['action'] for event in detail['events']],
            ['calculated', 'rejected', 'recalculated', 'cancelled', 'recalculated', 'reviewed', 'returned', 'recalculated', 'reviewed', 'approved'])
        self.assertTrue(detail['published'])
        self.assertNotIn('payments', detail)

    def test_approved_inputs_and_statutory_rules_flow_into_payslips(self):
        self.grant(*PAYROLL_CALCULATE, *PAYROLL_REVIEW_ADJUSTMENT, 'hrm.add_payrolladjustment', 'hrm.add_payrolldeductionrule',
                   'hrm.add_employeeloan', 'hrm.add_loaninstallment', 'hrm.view_employeeloan', 'hrm.change_payrolladjustment')
        bonus = SalaryComponent.objects.create(code='bonus', name='Bonus', type='earning')
        tax = SalaryComponent.objects.create(code='tax', name='Configured deduction', type='deduction')
        base = {'employee': self.employee.pk, 'currency': 'BDT', 'component': bonus.pk, 'effective_on': '2026-11-16',
                'reason': 'Approved source data'}
        adjustment = self.create_input('payroll-adjustment', {**base, 'source': 'bonus', 'amount': '1000'})
        self.assertEqual(self.preview()['payslips'][0]['gross_pay'], '49200.0000')
        overtime = self.create_input('payroll-adjustment', {**base, 'source': 'overtime', 'overtime_minutes': 120,
                                                           'hourly_rate': '250', 'multiplier': '1.5'})
        self.assertEqual(overtime['amount'], '750.0000')
        for row in [adjustment, overtime]:
            response = self.request_api('payroll-adjustment-review', {'status': 'approved'}, row['id'])
            self.assertEqual(response.status_code, 200, response.data)
        locked = self.client.patch(reverse('hrm:payroll-adjustment-detail', args=[adjustment['id']]), {'amount': '80'},
                                   format='json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(locked.status_code, 409)
        self.create_input('payroll-deduction-rule', {'salary_structure': self.structure.pk, 'component': tax.pk,
            'method': 'percentage_of_gross', 'value': '10', 'effective_from': '2026-11-01', 'reference': 'Demo policy'})
        loan = self.create_input('employee-loan', {'employee': self.employee.pk, 'currency': 'BDT', 'reference': 'LOAN-1',
                                                  'principal': '2000', 'disbursed_on': '2026-11-01'})
        installment = self.create_input('loan-installment', {'loan': loan['id'], 'month': '2026-11-01', 'amount': '500'})
        invalid = self.request_api('loan-installment-list', {'loan': loan['id'], 'month': '2026-12-01', 'amount': '2000'})
        self.assertEqual(invalid.status_code, 400)
        slip, = self.preview()['payslips']
        self.assertEqual((slip['gross_pay'], slip['total_deductions'], slip['net_pay']), ('50950.0000', '5420.0000', '45530.0000'))
        self.assertTrue(any(line['calculation_inputs'][0].get('installment_id') == installment['id'] for line in slip['lines']))
        self.assertContains(self.client.get(reverse('hrm_pages:payroll-adjustment-list')), 'Payroll adjustment list')
        for path in ('payroll-adjustment', 'payroll-deduction-rule', 'employee-loan', 'loan-installment'):
            self.grant('hrm.view_' + path.replace('-', ''))
            page = self.client.get(reverse(f'hrm_pages:add-{path}'))
            self.assertContains(page, f'action="{reverse(f"hrm:{path}-list")}"')
            self.assertContains(page, f'data-options-url="{reverse(f"hrm:{path}-options")}"')
            self.assertEqual(self.client.get(reverse(f'hrm:{path}-options')).status_code, 200)

    def test_payment_attempts_reserve_balance_and_recover_loan_when_settled(self):
        self.grant(*PAYROLL_CALCULATE, *PAYROLL_REVIEW, *PAYROLL_APPROVE, *PAYROLL_PAYMENT_WRITE,
                   'hrm.add_employeeloan', 'hrm.add_loaninstallment', 'hrm.view_employeeloan')
        loan = self.create_input('employee-loan', {'employee': self.employee.pk, 'currency': 'BDT', 'reference': 'LOAN-2',
                                                  'principal': '2000', 'disbursed_on': '2026-11-01'})
        installment = self.create_input('loan-installment', {'loan': loan['id'], 'month': '2026-11-01', 'amount': '500'})
        run = self.saved_and_approved()
        slip = Payslip.objects.get(payroll_run_id=run)
        self.assertEqual(slip.net_pay, Decimal('48700'))

        def start(amount, key=None):
            return self.request_api('salary-payment-create', {'payslip': slip.pk, 'amount': amount, 'method': 'bank',
                                                              'request_key': str(key or uuid4())}, run)

        def resolve(attempt, **result):
            return self.request_api('salary-payment-resolve', result, run, attempt)

        key = uuid4()
        failed = start('10000', key)
        self.assertEqual(failed.status_code, 201, failed.data)
        self.assertEqual(start('10000', key).data['id'], failed.data['id'])
        self.assertEqual(start('40000').status_code, 409)
        self.assertEqual(resolve(failed.data['id'], status='failed', failure_reason='Bank returned the transfer').status_code, 200)
        partial = start('10000')
        self.assertEqual(partial.status_code, 201, partial.data)
        result = {'status': 'succeeded', 'reference': 'PART-1', 'paid_on': '2026-11-18'}
        self.assertEqual(resolve(partial.data['id'], **result).status_code, 200)
        self.assertEqual(resolve(partial.data['id'], **result).status_code, 200)
        self.assertEqual(PayrollRun.objects.get(pk=run).status, 'payment_processing')
        self.assertIsNone(LoanInstallment.objects.get(pk=installment['id']).recovered_at)
        remaining = start('38700')
        self.assertEqual(remaining.status_code, 201, remaining.data)
        self.assertEqual(resolve(remaining.data['id'], **result).status_code, 409)
        result['reference'] = 'PART-2'
        self.assertEqual(resolve(remaining.data['id'], **result).status_code, 200)
        self.assertEqual(PayrollRun.objects.get(pk=run).status, 'paid')
        self.assertIsNotNone(LoanInstallment.objects.get(pk=installment['id']).recovered_at)
        loan_view = self.client.get(reverse('hrm:employee-loan-detail', args=[loan['id']])).data
        self.assertEqual(loan_view['balance'], '1500.0000')

    def test_reports_publish_only_approved_snapshots_and_offer_pdf_and_csv(self):
        self.grant(*PAYROLL_CALCULATE, *PAYROLL_REVIEW, *PAYROLL_APPROVE)
        report = reverse('hrm:payroll-report')
        saved = self.save(self.preview())
        run = saved.data['id']
        slip = Payslip.objects.get(payroll_run_id=run)
        pdf = reverse('report:payslip-pdf', args=[slip.pk])
        self.assertEqual(self.client.get(report, self.month).data['payslips'], [])
        self.assertEqual(self.client.get(pdf).status_code, 404)
        self.assertEqual(self.transition(run, 'review').status_code, 200)
        self.assertEqual(self.transition(run, 'approve').status_code, 200)
        self.assertContains(self.client.get(reverse('hrm_pages:payroll-report')), 'Payroll reports')
        response = self.client.get(report, {'employee_code': self.employee.employee_code})
        self.assertEqual(response.data['summary'][0]['net_pay'], '49200.0000')
        self.assertEqual(response.data['departments'][0]['department'], 'Unassigned')
        self.assertContains(self.client.get(report, {**self.month, 'export': 'csv'}), 'employee_code,employee_name')
        response = self.client.get(pdf, HTTP_ACCEPT='application/pdf')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
        self.assertIn('no-store', response['Cache-Control'])

    def test_approved_payslip_correction_can_pay_a_former_employee_in_a_later_period(self):
        self.grant(*PAYROLL_CALCULATE, *PAYROLL_REVIEW, *PAYROLL_APPROVE, *PAYROLL_REVIEW_ADJUSTMENT, 'hrm.add_payrolladjustment')
        Employee.objects.filter(pk=self.employee.pk).update(leaving_date=date(2026, 11, 30))
        run = self.saved_and_approved()
        slip = Payslip.objects.get(payroll_run_id=run)
        component = SalaryComponent.objects.create(code='correction', name='Pay correction', type='earning')
        adjustment = self.create_input('payroll-adjustment', {'employee': self.employee.pk, 'currency': 'BDT',
            'component': component.pk, 'effective_on': '2026-12-01', 'source': 'correction', 'amount': '500',
            'correction_of': slip.pk, 'reason': 'Adjustment to the approved November payslip'})
        self.assertEqual(self.request_api('payroll-adjustment-review', {'status': 'approved'}, adjustment['id']).status_code, 200)
        december = self.post('preview', {**self.month, 'month': '2026-12-01'}).data
        self.assertTrue(december['can_calculate'])
        self.assertEqual(december['payslips'][0]['net_pay'], '500.0000')
        self.assertEqual(Payslip.objects.get(pk=slip.pk).net_pay, Decimal('49200'))
