from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from hrm.models import Employee, EmployeeCompensation, SalaryComponent, SalaryStructure, SalaryStructureLine
from inventory.models import Currency


class SalaryConfigurationAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='salary-operator')
        cls.role = Group.objects.create(name='Salary operators')
        cls.user.groups.add(cls.role)
        cls.employee = Employee.objects.create(employee_code='PAY-1', full_name='Salaried employee')
        cls.basic = SalaryComponent.objects.create(code='basic', name='Basic salary', type='earning')
        cls.rent = SalaryComponent.objects.create(code='rent', name='House rent', type='earning')
        cls.structure = SalaryStructure.objects.create(code='monthly', name='Monthly payroll', currency_id='BDT')
        cls.basic_line = SalaryStructureLine.objects.create(
            salary_structure=cls.structure, component=cls.basic, calculation_method='basic_salary',
        )

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def grant(self, model, *actions):
        self.role.permissions.add(*Permission.objects.filter(
            content_type__app_label='hrm',
            codename__in=[f'{action}_{model._meta.model_name}' for action in actions],
        ))

    def compensation_payload(self, **changes):
        return {'employee': self.employee.pk, 'salary_structure': self.structure.pk, 'basic_salary': '30000',
                'effective_from': '2026-11-01', **changes}

    def test_each_crud_action_requires_its_existing_django_permission(self):
        no_csrf = APIClient(enforce_csrf_checks=True)
        no_csrf.force_login(self.user)
        no_csrf.get(reverse('account:login'))
        resources = [
            (SalaryComponent, 'salary-component', {'code': ' BONUS ', 'name': 'Bonus', 'type': 'earning'},
             {'category': 'incentives'}),
            (SalaryStructure, 'salary-structure', {'code': 'standard', 'name': 'Standard', 'currency': 'BDT'},
             {'description': 'Standard compensation'}),
            (SalaryStructureLine, 'salary-structure-line',
             {'salary_structure': self.structure.pk, 'component': self.rent.pk,
              'calculation_method': 'percentage_of_basic', 'percentage': '40'}, {'percentage': '35.0000'}),
            (EmployeeCompensation, 'employee-compensation', self.compensation_payload(), {'basic_salary': '35000.0000'}),
        ]
        for model, resource, payload, patch in resources:
            with self.subTest(resource=resource):
                collection = reverse(f'hrm:{resource}-list')
                listing = reverse(f'hrm:{resource}-list-view')
                self.assertEqual(APIClient().get(collection).status_code, 403)
                self.assertEqual(self.client.get(listing).status_code, 403)
                self.assertEqual(self.client.post(collection, payload, format='json').status_code, 403)

                self.grant(model, 'add')
                self.assertEqual(no_csrf.post(collection, payload, format='json').status_code, 403)
                response = self.client.post(collection, {**payload, 'create_by': 'spoofed'}, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                record = response.data[resource.replace('-', '_')]
                self.assertEqual(record['create_by'], self.user.username)
                detail = reverse(f'hrm:{resource}-detail', args=[record['id']])
                update = reverse(f'hrm:{resource}-update', args=[record['id']])

                self.assertEqual(self.client.get(detail).status_code, 403)
                self.assertEqual(self.client.patch(detail, patch, format='json').status_code, 403)
                self.assertEqual(self.client.put(update, payload, format='json').status_code, 403)
                self.assertEqual(self.client.delete(detail).status_code, 403)

                self.grant(model, 'view')
                self.assertEqual(self.client.get(collection).status_code, 200)
                self.assertEqual(self.client.get(listing).status_code, 200)
                response = self.client.get(detail)
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(self.client.patch(update, patch, format='json').status_code, 403)

                self.grant(model, 'change')
                response = self.client.put(update, {**payload, **patch}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['modified_by'], self.user.username)
                for field, value in patch.items():
                    self.assertEqual(response.data[field], value)
                self.assertEqual(self.client.delete(detail).status_code, 403)

                self.grant(model, 'delete')
                self.assertEqual(self.client.delete(detail).status_code, 204)
                self.assertEqual(self.client.get(detail).status_code, 404)

    def test_compensation_rejects_overlap_and_filters_effective_dates(self):
        direct_permission = Permission.objects.get(content_type__app_label='hrm', codename='view_employeecompensation')
        self.user.user_permissions.add(direct_permission)
        self.grant(EmployeeCompensation, 'add', 'change')
        url = reverse('hrm:employee-compensation-list')
        response = self.client.post(url, self.compensation_payload(effective_to='2026-11-15'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        first_pk = response.data['employee_compensation']['id']
        response = self.client.post(url, self.compensation_payload(effective_from='2026-11-15'), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('effective_from', response.data)
        response = self.client.post(url, self.compensation_payload(effective_from='2026-11-16'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.get(url, {'employee': self.employee.pk, 'from_date': '2026-11-16'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['currency'], 'BDT')

        detail = reverse('hrm:employee-compensation-detail', args=[first_pk])
        self.assertEqual(self.client.patch(detail, {'effective_to': None}, format='json').status_code, 400)
        self.employee.delete()
        response = self.client.patch(detail, {'basic_salary': '31000'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['employee_name'])
        self.assertEqual(self.client.post(url, self.compensation_payload(), format='json').status_code, 400)

    def test_in_use_rules_cannot_be_removed_or_silently_redenominated(self):
        self.grant(SalaryComponent, 'change', 'delete')
        self.grant(SalaryStructure, 'change', 'delete')
        self.grant(SalaryStructureLine, 'change', 'delete')
        fixed_line = SalaryStructureLine.objects.create(salary_structure=self.structure, component=self.rent,
                                                        calculation_method='fixed', fixed_amount=100)
        EmployeeCompensation.objects.create(employee=self.employee, salary_structure=self.structure,
                                            basic_salary=30000, effective_from='2026-11-01')
        other_currency, _ = Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar'})
        other_structure = SalaryStructure.objects.create(code='other', name='Other structure', currency=other_currency)
        structure_url = reverse('hrm:salary-structure-detail', args=[self.structure.pk])
        component_url = reverse('hrm:salary-component-detail', args=[self.basic.pk])
        line_url = reverse('hrm:salary-structure-line-detail', args=[self.basic_line.pk])
        fixed_line_url = reverse('hrm:salary-structure-line-detail', args=[fixed_line.pk])

        response = self.client.patch(component_url, {'type': 'deduction'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('type', response.data)
        self.assertEqual(self.client.patch(structure_url, {'currency': other_currency.code}, format='json').status_code, 409)
        self.assertEqual(self.client.patch(line_url, {'calculation_method': 'fixed', 'fixed_amount': '100'},
                                           format='json').status_code, 409)
        response = self.client.patch(fixed_line_url, {'salary_structure': other_structure.pk}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('fixed_amount', response.data)
        for url in (line_url, component_url, structure_url):
            self.assertEqual(self.client.delete(url).status_code, 409)
