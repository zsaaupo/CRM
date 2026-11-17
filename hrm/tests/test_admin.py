from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from hrm.models import Department, Designation, Employee, EmployeeStatus, EmploymentType


class EmployeeAdminTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser(username='hr-admin')
        self.client.force_login(self.root)
        self.employee = Employee.objects.create(employee_code='EMP-001', full_name='Archived Person')
        self.current = Employee.objects.create(employee_code='EMP-002', full_name='Current Person')
        self.employee.delete(actor='hr-manager')

    def test_admin_lists_archived_employees_and_can_filter_them(self):
        url = reverse('admin:hrm_employee_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Archived Person')
        self.assertContains(response, 'Current Person')
        response = self.client.get(url, {'is_archived__exact': '1'})
        self.assertContains(response, 'Archived Person')
        self.assertNotContains(response, 'Current Person')
        detail = self.client.get(reverse('admin:hrm_employee_change', args=[self.employee.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'hr-manager')
        self.assertNotContains(detail, 'name="_save"')
        self.assertNotContains(detail, 'class="deletelink"')

    def test_admin_single_and_bulk_delete_archive_without_removing_reporting_history(self):
        report = Employee.objects.create(employee_code='EMP-003', full_name='Report', reports_to=self.current)
        url = reverse('admin:hrm_employee_delete', args=[self.current.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['protected'])
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)
        self.current.refresh_from_db()
        report.refresh_from_db()
        self.assertTrue(self.current.is_archived)
        self.assertEqual(self.current.archived_by, self.root.username)
        self.assertEqual(report.reports_to_id, self.current.pk)
        response = self.client.post(reverse('admin:hrm_employee_changelist'), {
            'action': 'delete_selected', '_selected_action': [report.pk, self.employee.pk], 'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertTrue(report.is_archived)
        self.assertEqual(Employee.all_objects.count(), 3)

    def test_archived_admin_records_cannot_be_changed_or_deleted_again(self):
        change = reverse('admin:hrm_employee_change', args=[self.employee.pk])
        delete = reverse('admin:hrm_employee_delete', args=[self.employee.pk])
        self.assertEqual(self.client.post(change, {'full_name': 'Rewritten'}).status_code, 403)
        self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 403)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.full_name, 'Archived Person')
        self.assertEqual(self.employee.archived_by, 'hr-manager')

    def test_staff_need_employee_view_permission_to_see_archives(self):
        staff = get_user_model().objects.create_user(username='staff', is_staff=True)
        self.client.force_login(staff)
        url = reverse('admin:hrm_employee_changelist')
        self.assertEqual(self.client.get(url).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='hrm', codename='view_employee'))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Archived Person')

    def test_hrm_lookups_are_registered_and_referenced_records_are_protected(self):
        for model in [Department, Designation, EmploymentType, EmployeeStatus]:
            self.assertIn(model, admin.site._registry)
        status = EmployeeStatus.objects.get(code='active')
        response = self.client.get(reverse('admin:hrm_employeestatus_delete', args=[status.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['protected'])
        self.assertTrue(EmployeeStatus.objects.filter(pk=status.pk).exists())
