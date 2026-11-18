from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from hrm.models import Employee, EmployeeSalary, PayrollRun, Payslip, PayslipLine, SalaryPayment
from inventory.models import Currency


class PayrollModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = Employee.objects.create(employee_code='PAY-1', full_name='Payroll Employee')
        cls.currency = Currency.objects.create(code='EUR', name='Euro')
        cls.salary = EmployeeSalary.objects.create(
            employee=cls.employee, currency=cls.currency, monthly_amount=Decimal('25000.0000'),
            effective_from=date(2026, 11, 1),
        )
        cls.payroll = PayrollRun.objects.create(month=date(2026, 11, 1), currency=cls.currency)
        cls.reviewer = get_user_model().objects.create_user(username='payroll-approver')

    def validate(self, record):
        record.full_clean(exclude=['create_by', 'modified_by'])

    def payslip(self, **changes):
        return Payslip(**{
            'payroll_run': self.payroll, 'employee': self.employee, 'salary': self.salary,
            'base_salary': Decimal('25000.0000'), 'total_earnings': Decimal('25500.1500'),
            'total_deductions': Decimal('200.1000'), 'net_pay': Decimal('25300.0500'), **changes,
        })

    def test_salary_changes_keep_distinct_effective_dates_and_payslip_amounts(self):
        slip = self.payslip()
        self.validate(slip)
        slip.save()
        PayslipLine.objects.create(payslip=slip, kind=PayslipLine.Kind.EARNING,
                                   description='Bonus', amount=Decimal('500.1500'))
        next_rate = EmployeeSalary.objects.create(
            employee=self.employee, currency=self.currency, monthly_amount=Decimal('26000.0000'),
            effective_from=date(2026, 12, 1),
        )
        self.assertEqual(self.employee.salary_rates.first(), next_rate)
        slip.refresh_from_db()
        self.assertEqual(slip.salary, self.salary)
        self.assertEqual(slip.base_salary, Decimal('25000.0000'))
        self.assertEqual(slip.net_pay, Decimal('25300.0500'))
        with self.assertRaises(ProtectedError):
            self.salary.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.payslip().save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeSalary.objects.create(employee=self.employee, currency=self.currency,
                                          monthly_amount=1, effective_from=self.salary.effective_from)

    def test_payslip_checks_currency_salary_owner_effective_month_and_net_pay(self):
        different_employee = Employee.objects.create(employee_code='PAY-2', full_name='Other Employee')
        other_currency = Currency.objects.create(code='CHF', name='Swiss franc')
        next_month = PayrollRun.objects.create(month=date(2026, 12, 1), currency=other_currency)
        later_rate = EmployeeSalary.objects.create(employee=self.employee, currency=self.currency,
                                                   monthly_amount=30000, effective_from=date(2026, 12, 1))
        for field, changes in [
            ('salary', {'employee': different_employee}), ('salary', {'payroll_run': next_month}),
            ('salary', {'salary': later_rate}), ('net_pay', {'net_pay': Decimal('25300.0400')}),
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError) as error:
                self.validate(self.payslip(**changes))
            self.assertIn(field, error.exception.message_dict)

    def test_run_is_monthly_unique_and_approval_requires_actor_and_time(self):
        self.assertEqual(self.payroll.status, PayrollRun.Status.DRAFT)
        for month in [date(2026, 11, 1), date(2026, 11, 2)]:
            with self.subTest(month=month), self.assertRaises(IntegrityError), transaction.atomic():
                PayrollRun.objects.create(month=month, currency=self.currency)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayrollRun.objects.filter(pk=self.payroll.pk).update(status=PayrollRun.Status.APPROVED)

    def test_partial_payments_require_approval_and_cannot_exceed_net_pay(self):
        slip = self.payslip()
        slip.save()
        payment = SalaryPayment(payslip=slip, amount=Decimal('10000.0000'), reference='PAY-01')
        with self.assertRaises(ValidationError) as error:
            self.validate(payment)
        self.assertIn('payslip', error.exception.message_dict)

        self.payroll.status = PayrollRun.Status.APPROVED
        self.payroll.approved_by = self.reviewer
        self.payroll.approved_at = timezone.now()
        self.validate(self.payroll)
        self.payroll.save()
        self.validate(payment)
        payment.save()
        self.validate(payment)
        remainder = SalaryPayment(payslip=slip, amount=Decimal('15300.0500'), reference='PAY-02')
        self.validate(remainder)
        remainder.save()
        with self.assertRaises(ValidationError) as error:
            self.validate(SalaryPayment(payslip=slip, amount=Decimal('0.0001')))
        self.assertIn('amount', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalaryPayment.objects.create(payslip=slip, amount=10, reference='PAY-01')
