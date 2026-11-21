from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from hrm.catalog import RESOURCE_MAP, SALARY_RESOURCES
from hrm.models import Employee, EmployeeCompensation, SalaryComponent, SalaryStructure, SalaryStructureLine


class SalaryPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='salary-pages')
        cls.role = Group.objects.create(name='Salary configuration staff')
        cls.user.groups.add(cls.role)
        cls.employee = Employee.objects.create(employee_code='SAL-PAGE', full_name='Salary test employee')

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def grant(self, key, *actions):
        self.role.permissions.add(*Permission.objects.filter(
            content_type__app_label='hrm',
            codename__in=[RESOURCE_MAP[key].permission(action).split('.')[1] for action in actions],
        ))

    def test_sidebar_and_add_action_follow_salary_permissions(self):
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-salary-toggle')
        self.grant('salary-component', 'view')
        component_list = reverse('hrm_pages:salary-component-list')
        add_component = reverse('hrm_pages:add-salary-component')
        response = self.client.get(component_list)
        self.assertContains(response, 'data-hrm-toggle')
        self.assertContains(response, 'data-salary-toggle aria-controls="hrm-salary-submenu" aria-expanded="true"')
        self.assertContains(response, 'aria-current="page">Salary component list')
        self.assertNotContains(response, 'Salary structure list')
        self.assertNotContains(response, f'href="{add_component}"')
        self.assertEqual(self.client.get(add_component).status_code, 403)
        self.assertEqual(self.client.get(reverse('hrm:salary-component-options')).status_code, 403)
        self.grant('salary-component', 'add')
        self.assertContains(self.client.get(component_list), f'href="{add_component}" class="primary-button compact-primary"')

    def test_loan_navigation_is_separate_and_uses_existing_api_routes(self):
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-loan-toggle')
        for key in ('employee-loan', 'loan-installment'):
            with self.subTest(resource=key):
                self.grant(key, 'view', 'add')
                response = self.client.get(reverse(f'hrm_pages:{key}-list'))
                self.assertContains(response, 'data-hrm-toggle')
                self.assertContains(response, 'data-loan-toggle aria-controls="hrm-loan-submenu" aria-expanded="true"')
                self.assertNotContains(response, 'data-salary-toggle')
                self.assertContains(response, f'data-api-url="{reverse(f"hrm:{key}-list-view")}"')
                form = self.client.get(reverse(f'hrm_pages:add-{key}'))
                self.assertContains(form, f'action="{reverse(f"hrm:{key}-list")}"')
                self.assertContains(form, f'data-options-url="{reverse(f"hrm:{key}-options")}"')

    def test_each_salary_form_creates_through_its_api_and_points_back_to_its_list(self):
        payloads = {
            'salary-component': {'code': 'basic', 'name': 'Basic Salary', 'type': 'earning'},
            'salary-structure': {'code': 'monthly', 'name': 'Monthly Salary', 'currency': 'BDT'},
            'salary-structure-line': {'calculation_method': 'basic_salary', 'fixed_amount': None, 'percentage': None},
            'employee-compensation': {'employee': self.employee.pk, 'basic_salary': '30000', 'effective_from': '2026-12-01',
                                      'effective_to': None},
        }
        option_fields = {
            'salary-component': {'type'},
            'salary-structure': {'currency'},
            'salary-structure-line': {'salary_structure', 'component', 'calculation_method'},
            'employee-compensation': {'salary_structure', 'employee'},
        }
        ids = {}
        for resource in SALARY_RESOURCES:
            key = resource.key
            with self.subTest(resource=key):
                self.grant(key, 'view', 'add', 'change')
                list_url = reverse(f'hrm_pages:{key}-list')
                add_url = reverse(f'hrm_pages:add-{key}')
                create_url = reverse(f'hrm:{key}-list')
                options_url = reverse(f'hrm:{key}-options')
                self.assertContains(self.client.get(list_url), f'href="{add_url}" class="primary-button compact-primary"')
                form = self.client.get(add_url)
                self.assertContains(form, f'action="{create_url}"')
                self.assertContains(form, 'data-method="POST"')
                self.assertContains(form, f'data-success-url="{list_url}"')
                self.assertContains(form, f'data-options-url="{options_url}"')
                options = self.client.get(options_url)
                self.assertEqual(options.status_code, 200)
                self.assertEqual(set(options.data), option_fields[key])
                for field in option_fields[key]:
                    self.assertContains(form, f'data-options-field="{field}"')
                    self.assertTrue(options.data[field], field)
                payload = payloads[key]
                if key in ('salary-structure-line', 'employee-compensation'):
                    payload['salary_structure'] = ids['salary-structure']
                if key == 'salary-structure-line':
                    payload['component'] = ids['salary-component']
                self.assertEqual(self.client.post(create_url, payload, format='json').status_code, 403)
                response = self.client.post(create_url, payload, format='json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
                self.assertEqual(response.status_code, 201, response.data)
                ids[key] = response.data[key.replace('-', '_')]['id']
                list_api = reverse(f'hrm:{key}-list-view')
                self.assertContains(self.client.get(list_url), f'data-api-url="{list_api}"')
                self.assertIn(ids[key], [row['id'] for row in self.client.get(list_api).data['results']])
                edit = self.client.get(reverse(f'hrm_pages:edit-{key}', args=[ids[key]]))
                edit_options = reverse(f'hrm:{key}-edit-options', args=[ids[key]])
                self.assertContains(edit, f'data-options-url="{edit_options}"')
                self.assertEqual(self.client.get(edit_options).status_code, 200)

    def test_employee_compensation_choices_offer_complete_structures_and_retain_history(self):
        self.grant('employee-compensation', 'view', 'add')
        component = SalaryComponent.objects.create(code='basic', name='Basic Salary', type='earning')
        structure = SalaryStructure.objects.create(code='monthly', name='Monthly Salary', currency_id='BDT')
        SalaryStructure.objects.create(code='incomplete', name='Incomplete', currency_id='BDT')
        SalaryStructureLine.objects.create(salary_structure=structure, component=component, calculation_method='basic_salary')
        compensation = EmployeeCompensation.objects.create(employee=self.employee, salary_structure=structure,
                                                             basic_salary=30000, effective_from='2026-12-01')
        add_options = reverse('hrm:employee-compensation-options')
        edit_options = reverse('hrm:employee-compensation-edit-options', args=[compensation.pk])
        self.assertEqual(self.client.get(add_options).data['salary_structure'],
                         [{'value': structure.pk, 'label': 'Monthly Salary (monthly)'}])
        self.assertEqual(self.client.get(edit_options).status_code, 403)
        self.employee.delete()
        structure.is_active = False
        structure.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(add_options).data, {'employee': [], 'salary_structure': []})
        self.grant('employee-compensation', 'change')
        self.assertEqual(self.client.get(edit_options).data, {
            'employee': [{'value': self.employee.pk, 'label': 'Archived employee (existing)'}],
            'salary_structure': [{'value': structure.pk, 'label': 'Monthly Salary (monthly)'}],
        })
