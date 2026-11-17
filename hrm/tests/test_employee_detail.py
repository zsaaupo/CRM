from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from hrm.models import Department, Designation, Employee
from warehouse.models import WareHouse


class EmployeeDetailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='employee-reader')
        self.client.force_login(self.user)
        self.manager = Employee.objects.create(employee_code='MANAGER', full_name='Reporting Manager')
        self.employee = Employee.objects.create(
            employee_code='EMP-DETAIL', full_name='Example Employee', work_email='employee@example.com',
            department=Department.objects.create(code='ops', name='Operations'),
            designation=Designation.objects.create(code='officer', name='Officer'),
            warehouse=WareHouse.objects.create(name='UTT', address='Uttara', phone='123', code='UTT'),
            reports_to=self.manager, user=self.user, image='employee_pictures/example.png',
        )
        self.url = reverse('hrm_pages:employee-detail', args=[self.employee.pk])
        self.api_url = reverse('hrm:employee-detail', args=[self.employee.pk])

    def grant(self, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='hrm',
            codename__in=[f'{action}_employee' for action in actions]))

    def test_view_grant_enables_row_action_and_api_backed_page_without_write_permissions(self):
        self.assertRedirects(Client().get(self.url), reverse('account_pages:login'))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.api_url).status_code, 403)
        self.grant('view')
        listing = self.client.get(reverse('hrm_pages:employee-list'))
        self.assertContains(listing, 'data-view-url-template="/hrm/employees/0/"')
        self.assertEqual(sum(column['key'] == 'actions' for column in listing.context['columns']), 1)
        self.assertNotIn('edit_url_template', listing.context)
        self.assertNotIn('delete_url_template', listing.context)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertIn('no-store', page['Cache-Control'])
        self.assertContains(page, f'data-api-url="{self.api_url}"')
        self.assertContains(page, 'data-title-field="full_name"')
        self.assertContains(page, 'data-record-content hidden')
        self.assertContains(page, '/static/hrm/js/employee_detail.js')
        self.assertContains(page, 'aria-current="page">Employee list')
        self.assertContains(page, 'Back to employees')
        self.assertNotContains(page, self.employee.full_name)
        self.assertNotIn('edit_url', page.context)
        for field in ['employee_code', 'full_name', 'work_email', 'work_phone', 'department_name',
                      'designation_name', 'employment_type_name', 'status_name', 'joining_date', 'leaving_date',
                      'reports_to_name', 'workplace', 'warehouse_name', 'user_username', 'create_by']:
            self.assertContains(page, f'data-detail-field="{field}"')
        self.grant('change', 'delete')
        self.assertContains(self.client.get(self.url),
            f'href="{reverse("hrm_pages:edit-employee", args=[self.employee.pk])}"')
        listing = self.client.get(reverse('hrm_pages:employee-list'))
        self.assertEqual(sum(column['key'] == 'actions' for column in listing.context['columns']), 1)

    def test_detail_api_labels_are_read_only_and_optional_values_are_nullable(self):
        self.grant('view', 'change')
        data = self.client.get(self.api_url).json()
        expected = {'full_name': 'Example Employee', 'department_name': 'Operations', 'designation_name': 'Officer',
                    'warehouse_name': 'UTT', 'reports_to_name': 'Reporting Manager', 'user_username': self.user.username,
                    'leaving_date': None}
        for name, value in expected.items():
            self.assertEqual(data[name], value)
        self.assertTrue(data['image'].endswith('/employee_pictures/example.png'))
        empty = self.client.get(reverse('hrm:employee-detail', args=[self.manager.pk])).json()
        for field in ['department_name', 'designation_name', 'warehouse_name', 'reports_to_name', 'user_username', 'image']:
            self.assertIsNone(empty[field])
        page = self.client.get(self.url)
        response = self.client.patch(self.api_url, {'reports_to_name': 'Spoof', 'user_username': 'Spoof'},
            content_type='application/json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['reports_to_name'], 'Reporting Manager')
        self.assertEqual(response.json()['user_username'], self.user.username)

    def test_archived_or_missing_employee_is_unavailable_and_archived_manager_name_is_hidden(self):
        self.grant('view')
        self.manager.delete(actor='tester')
        response = self.client.get(self.api_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['reports_to_name'])
        self.assertNotIn('Reporting Manager', response.content.decode())
        for root in [False, True]:
            self.user.is_superuser = root
            self.user.save(update_fields=['is_superuser'])
            for pk in [self.manager.pk, 999999]:
                with self.subTest(root=root, pk=pk):
                    self.assertEqual(self.client.get(reverse('hrm_pages:employee-detail', args=[pk])).status_code, 404)
                    self.assertEqual(self.client.get(reverse('hrm:employee-detail', args=[pk])).status_code, 404)

    def test_role_permission_revocation_blocks_detail_page_and_api(self):
        role = Group.objects.create(name='Employee readers')
        role.permissions.add(Permission.objects.get(content_type__app_label='hrm', codename='view_employee'))
        self.user.groups.add(role)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(self.api_url).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.api_url).status_code, 403)
