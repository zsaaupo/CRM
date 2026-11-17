from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from hrm.models import Department, Employee, EmployeeStatus, EmploymentType
from warehouse.models import WareHouse


class EmployeeOptionsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='employee-options')
        self.client = APIClient()
        self.client.force_login(self.user)
        self.url = reverse('hrm:employee-options')
        self.department = Department.objects.create(code='ops', name='Operations')
        self.inactive = Department.objects.create(code='old', name='Old department', is_active=False)
        self.manager = Employee.objects.create(employee_code='MANAGER', full_name='Manager')
        self.archived = Employee.objects.create(employee_code='ARCHIVED', full_name='Hidden Person')
        self.archived.delete(actor='tester')
        self.warehouse = WareHouse.objects.create(name='UTT', code='UTT', address='Uttara', phone='123')

    def grant(self, *codes, app='hrm'):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label=app, codename__in=codes))

    def test_options_need_add_permission_and_only_expose_minimal_choices(self):
        self.assertEqual(APIClient().get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant('add_employee')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response.data['department'], [{'value': self.department.pk, 'label': 'Operations'}])
        self.assertEqual(response.data['reports_to'], [{'value': self.manager.pk, 'label': 'Manager (MANAGER)'}])
        self.assertEqual(response.data['user'], [])
        self.assertNotIn('Hidden Person', str(response.data))
        for field in ['employment_type', 'status', 'designation', 'warehouse']:
            self.assertIn(field, response.data)
            for choice in response.data[field]:
                self.assertEqual(set(choice), {'value', 'label'})
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)

    def test_edit_options_need_view_and_change_and_include_existing_inactive_values(self):
        employee = Employee.objects.create(employee_code='EMP', full_name='Employee', department=self.inactive)
        url = reverse('hrm:employee-edit-options', args=[employee.pk])
        self.grant('change_employee')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.grant('view_employee')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn({'value': self.inactive.pk, 'label': 'Old department'}, response.data['department'])
        self.assertNotIn(employee.pk, [choice['value'] for choice in response.data['reports_to']])
        employee.delete(actor='tester')
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_existing_archived_manager_is_anonymous_and_can_be_preserved_in_an_edit(self):
        employee = Employee.objects.create(employee_code='EMP', full_name='Employee', reports_to=self.archived)
        self.grant('view_employee', 'change_employee')
        response = self.client.get(reverse('hrm:employee-edit-options', args=[employee.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn({'value': self.archived.pk, 'label': 'Archived employee (existing)'}, response.data['reports_to'])
        self.assertNotIn('Hidden Person', str(response.data))
        response = self.client.patch(reverse('hrm:employee-update', args=[employee.pk]),
            {'full_name': 'Updated', 'reports_to': self.archived.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch(reverse('hrm:employee-update', args=[self.manager.pk]),
            {'reports_to': self.archived.pk}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_login_choices_require_user_change_permission_exclude_assigned_logins_and_superusers(self):
        self.grant('add_employee')
        available = get_user_model().objects.create_user(username='available')
        linked = get_user_model().objects.create_user(username='linked')
        root = get_user_model().objects.create_superuser(username='root')
        self.archived.user = linked
        self.archived.save(update_fields=['user'])
        self.assertEqual(self.client.get(self.url).data['user'], [])
        self.grant('change_user', app='auth')
        choices = self.client.get(self.url).data['user']
        ids = {choice['value'] for choice in choices}
        self.assertIn(available.pk, ids)
        self.assertNotIn(linked.pk, ids)
        self.assertNotIn(root.pk, ids)

    def test_missing_required_lookup_choices_are_empty_and_recover_after_creation(self):
        self.grant('add_employee')
        EmployeeStatus.objects.filter(code='active').update(is_active=False)
        EmploymentType.objects.update(is_active=False)
        self.assertEqual(self.client.get(self.url).data['employment_type'], [])
        EmploymentType.objects.create(code='seasonal', name='Seasonal')
        self.assertEqual(self.client.get(self.url).data['employment_type'], [{'value': 'seasonal', 'label': 'Seasonal'}])
