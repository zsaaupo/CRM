from datetime import date, datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from audit_log.models import AuditLog
from hrm.models import Attendance, Employee


@override_settings(TIME_ZONE='Asia/Dhaka')
class AttendanceImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='attendance-importer')
        cls.employee = Employee.objects.create(employee_code='EMP-001', full_name='Night Shift')
        cls.other = Employee.objects.create(employee_code='EMP-002', full_name='Day Shift')
        cls.archived = Employee.objects.create(employee_code='EMP-003', full_name='Archived Employee')
        cls.archived.delete()

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.page = reverse('hrm_pages:import-attendance')
        self.list_page = reverse('hrm_pages:attendance-list')
        self.url = reverse('hrm:attendance-import')
        self.set_csrf()

    def grant(self, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='hrm', codename__in=[f'{action}_attendance' for action in actions],
        ))

    def set_csrf(self):
        self.client.get(reverse('dashboard'))
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)

    def upload(self, contents, filename='attendance.csv', **extra):
        if isinstance(contents, str):
            contents = contents.encode('utf-8')
        file = SimpleUploadedFile(filename, contents, content_type='text/csv')
        return self.client.post(self.url, {'file': file, **extra}, format='multipart')

    def test_permissions_template_and_list_link_follow_existing_attendance_forms(self):
        self.grant('view')
        self.assertNotContains(self.client.get(self.list_page), 'Import CSV')
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.upload('employee_code,work_date\nEMP-001,2026-11-18\n').status_code, 403)

        self.grant('add')
        self.assertContains(self.client.get(self.list_page), f'href="{self.page}"')
        page = self.client.get(self.page)
        self.assertContains(page, f'action="{self.url}"')
        self.assertContains(page, f'href="{self.url}" download')
        self.assertContains(page, 'Asia/Dhaka')
        template = self.client.get(self.url)
        self.assertEqual(template['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attendance-import-template.csv', template['Content-Disposition'])
        self.assertEqual(template.content.decode('utf-8-sig').strip(),
                         'employee_code,work_date,status,check_in,check_out,notes')

        self.client.credentials()
        response = self.upload('employee_code,work_date\nEMP-001,2026-11-18\n')
        self.assertEqual(response.status_code, 403)

    def test_imports_overnight_shifts_and_defaults_with_audit_events(self):
        self.grant('add')
        response = self.upload(
            '\ufeffemployee_code,work_date,status,check_in,check_out,notes\r\n'
            'emp-001,2026-11-18,present,2026-11-18 22:00:00,2026-11-19T00:00:00Z,"Handover, complete"\r\n'
            'EMP-002,2026-11-18,absent,,,\r\n'
            'EMP-002,2026-11-19,,,,\r\n', create_by='spoofed',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['imported_count'], 3)
        shift = Attendance.objects.get(employee=self.employee)
        self.assertEqual(shift.work_date, date(2026, 11, 18))
        self.assertEqual(shift.check_in, datetime(2026, 11, 18, 16, tzinfo=timezone.utc))
        self.assertEqual(shift.check_out, datetime(2026, 11, 19, tzinfo=timezone.utc))
        self.assertEqual(shift.notes, 'Handover, complete')
        self.assertEqual(shift.create_by, self.user.username)
        self.assertEqual(shift.modified_by, self.user.username)
        self.assertTrue(Attendance.objects.filter(employee=self.other, work_date='2026-11-19', status='unrecorded').exists())
        events = AuditLog.objects.filter(resource='hrm.attendance', action='created')
        self.assertEqual(events.count(), 3)
        self.assertEqual(set(events.values_list('actor', flat=True)), {self.user.username})

    def test_invalid_rows_and_duplicates_reject_the_whole_upload(self):
        self.grant('add')
        existing = Attendance.objects.create(employee=self.employee, work_date=date(2026, 11, 19), notes='Keep me')
        response = self.upload(
            'employee_code,work_date,status,check_in\n'
            'EMP-001,2026-11-18,,\n'
            'emp-001,2026-11-18,,\n'
            'EMP-001,2026-11-19,,\n'
            'EMP-003,2026-11-18,,\n'
            'EMP-002,2026-11-19,present,\n'
            'EMP-002,,absent,\n'
            'EMP-002,2026-11-20,absent,2026-11-20 09:00:00\n',
        )
        self.assertEqual(response.status_code, 400, response.data)
        errors = response.data['file']
        self.assertEqual([error.split(':', 1)[0] for error in errors], [f'Row {row}' for row in range(3, 9)])
        self.assertIn('row 2', errors[0])
        self.assertIn('already exists', errors[1])
        self.assertIn('current employee', errors[2])
        self.assertIn('must have a check-in', errors[3])
        self.assertEqual(list(Attendance.objects.values_list('pk', 'notes')), [(existing.pk, 'Keep me')])
        self.assertEqual(AuditLog.objects.filter(resource='hrm.attendance').count(), 1)

    def test_bad_csv_files_are_reported_as_upload_errors(self):
        self.grant('add')
        for contents, filename in (
            ('employee_code,wrong\nEMP-001,2026-11-18', 'attendance.csv'),
            ('employee_code,work_date\n', 'attendance.csv'),
            ('employee_code,work_date\nEMP-001,2026-11-18,extra', 'attendance.csv'),
            ('employee_code,work_date\n"EMP-001,2026-11-18', 'attendance.csv'),
            (b'employee_code,work_date\n\xff,2026-11-18', 'attendance.csv'),
            ('employee_code,work_date\nEMP-001,2026-11-18', 'attendance.txt'),
            ('employee_code,work_date\n' + 'EMP-001,2026-11-18\n' * 1001, 'attendance.csv'),
        ):
            with self.subTest(contents=contents[:100], filename=filename):
                response = self.upload(contents, filename)
                self.assertEqual(response.status_code, 400)
                self.assertIn('file', response.data)
        self.assertFalse(Attendance.objects.exists())
