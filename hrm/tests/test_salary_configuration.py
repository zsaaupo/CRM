from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from hrm.models import Employee, EmployeeCompensation, SalaryComponent, SalaryStructure, SalaryStructureLine


class SalaryConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = Employee.objects.create(employee_code='SAL-1', full_name='Payroll employee')
        cls.structure = SalaryStructure.objects.create(code='monthly', name='Monthly salary', currency_id='BDT')
        cls.basic = SalaryComponent.objects.create(code='basic', name='Basic salary', type=SalaryComponent.Kind.EARNING)
        cls.allowance = SalaryComponent.objects.create(code='rent', name='House rent', type=SalaryComponent.Kind.EARNING)
        cls.deduction = SalaryComponent.objects.create(code='loan', name='Loan', type=SalaryComponent.Kind.DEDUCTION)
        cls.basic_line = SalaryStructureLine.objects.create(
            salary_structure=cls.structure, component=cls.basic,
            calculation_method=SalaryStructureLine.CalculationMethod.BASIC_SALARY,
        )

    def line(self, component, method, **values):
        return SalaryStructureLine(
            salary_structure=self.structure, component=component, calculation_method=method,
            create_by='tester', modified_by='tester', **values,
        )

    def compensation(self, start, end=None, **changes):
        return EmployeeCompensation(**{
            'employee': self.employee, 'salary_structure': self.structure, 'basic_salary': Decimal('30000'),
            'effective_from': start, 'effective_to': end, 'create_by': 'tester', 'modified_by': 'tester', **changes,
        })

    def test_structure_supports_fixed_and_percentage_rules(self):
        methods = SalaryStructureLine.CalculationMethod
        rent = self.line(self.allowance, methods.PERCENTAGE_OF_BASIC, percentage=Decimal('40'))
        loan = self.line(self.deduction, methods.FIXED, fixed_amount=Decimal('1000'))
        for line in (rent, loan):
            line.full_clean()
            line.save()
        rent.refresh_from_db()
        loan.refresh_from_db()
        self.assertEqual(rent.percentage, Decimal('40'))
        self.assertEqual(loan.fixed_amount, Decimal('1000'))
        self.assertEqual(self.structure.lines.count(), 3)

    def test_rule_values_and_uniqueness_are_enforced_in_database(self):
        methods = SalaryStructureLine.CalculationMethod
        invalid_lines = (
            self.line(self.allowance, methods.FIXED),
            self.line(self.allowance, methods.FIXED, fixed_amount=Decimal('-1')),
            self.line(self.allowance, methods.PERCENTAGE_OF_BASIC, percentage=Decimal('40'), fixed_amount=Decimal('1')),
            self.line(self.allowance, methods.BASIC_SALARY),
            self.line(self.basic, methods.FIXED, fixed_amount=Decimal('1')),
        )
        for line in invalid_lines:
            with self.subTest(method=line.calculation_method, component=line.component_id):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    line.save()

    def test_basic_salary_requires_an_earning_component(self):
        line = self.line(self.deduction, SalaryStructureLine.CalculationMethod.BASIC_SALARY)
        with self.assertRaises(ValidationError) as error:
            line.full_clean()
        self.assertIn('component', error.exception.message_dict)

        self.basic.type = SalaryComponent.Kind.DEDUCTION
        with self.assertRaises(ValidationError) as error:
            self.basic.full_clean(exclude=['create_by', 'modified_by'])
        self.assertIn('type', error.exception.message_dict)

    def test_compensation_periods_are_inclusive_and_keep_previous_pay(self):
        previous = self.compensation(date(2026, 1, 1), date(2026, 1, 15))
        previous.full_clean()
        previous.save()

        overlap = self.compensation(date(2026, 1, 15))
        with self.assertRaises(ValidationError) as error:
            overlap.full_clean()
        self.assertIn('effective_from', error.exception.message_dict)

        current = self.compensation(date(2026, 1, 16), basic_salary=Decimal('33000'))
        current.full_clean()
        current.save()
        current.full_clean()
        self.assertEqual(list(self.employee.compensation_assignments.values_list('basic_salary', flat=True)),
                         [Decimal('33000'), Decimal('30000')])
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.compensation(date(2026, 2, 1)).save()

    def test_compensation_requires_a_basic_salary_line_and_valid_dates(self):
        incomplete = SalaryStructure.objects.create(code='incomplete', name='Incomplete', currency_id='BDT')
        compensation = self.compensation(date(2026, 1, 16), date(2026, 1, 15), salary_structure=incomplete)
        with self.assertRaises(ValidationError) as error:
            compensation.full_clean()
        self.assertIn('salary_structure', error.exception.message_dict)
        self.assertIn('effective_to', error.exception.message_dict)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.compensation(date(2026, 1, 16), basic_salary=Decimal('0')).save()
