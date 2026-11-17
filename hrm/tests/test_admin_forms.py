from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from hrm.models import Employee


class EmployeeAdminFormTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser(username='admin-form-root')
        self.target = get_user_model().objects.create_user(username='target-login')
        self.client.force_login(self.root)

    def payload(self, **changes):
        return {
            'employee_code': 'EMP-ADMIN', 'full_name': 'Admin Employee',
            'employment_type': 'full-time', 'status': 'active', 'joining_date': '2026-01-01',
            '_save': 'Save', **changes,
        }

    def test_admin_create_and_edit_preserve_audit_and_allow_authorized_login_link(self):
        response = self.client.post(reverse('admin:hrm_employee_add'), self.payload(user=self.target.pk))
        self.assertEqual(response.status_code, 302)
        employee = Employee.objects.get(employee_code='EMP-ADMIN')
        self.assertEqual(employee.user_id, self.target.pk)
        self.assertEqual(employee.create_by, self.root.username)
        self.assertEqual(employee.modified_by, self.root.username)
        response = self.client.post(reverse('admin:hrm_employee_change', args=[employee.pk]),
            self.payload(full_name='Updated Employee', user=''))
        self.assertEqual(response.status_code, 302)
        employee.refresh_from_db()
        self.assertEqual(employee.full_name, 'Updated Employee')
        self.assertIsNone(employee.user_id)

    def test_employee_only_admin_cannot_write_login_link(self):
        staff = get_user_model().objects.create_user(username='hr-only', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='hrm', codename='add_employee'))
        self.client.force_login(staff)
        response = self.client.post(reverse('admin:hrm_employee_add'), self.payload(user=self.target.pk))
        self.assertEqual(response.status_code, 302)
        employee = Employee.objects.get(employee_code='EMP-ADMIN')
        self.assertIsNone(employee.user_id)

    def test_non_superuser_admin_cannot_link_superuser_login(self):
        staff = get_user_model().objects.create_user(username='hr-user-manager', is_staff=True)
        staff.user_permissions.add(
            Permission.objects.get(content_type__app_label='hrm', codename='add_employee'),
            Permission.objects.get(content_type__app_label='auth', codename='change_user'))
        self.client.force_login(staff)
        response = self.client.post(reverse('admin:hrm_employee_add'), self.payload(user=self.root.pk))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Employee.objects.filter(employee_code='EMP-ADMIN').exists())
