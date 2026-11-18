from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from hrm.catalog import ATTENDANCE_RESOURCES, RESOURCE_MAP
from hrm.models import Employee, EmployeeScheduleAssignment, WorkSchedule


@override_settings(TIME_ZONE='Asia/Dhaka')
class AttendancePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='attendance-pages')
        cls.employee = Employee.objects.create(employee_code='ATT-001', full_name='Test Employee')
        cls.schedule = WorkSchedule.objects.create(code='office', name='Office', working_days=[0, 1],
                                                   start_time=time(9), end_time=time(18))

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def grant(self, key, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='hrm',
            codename__in=[RESOURCE_MAP[key].permission(action).split('.')[1] for action in actions]))

    def test_sidebar_shows_only_allowed_attendance_lists_and_opens_the_active_group(self):
        self.grant('holiday', 'view')
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, 'data-hrm-toggle')
        self.assertContains(dashboard, 'data-attendance-toggle')
        self.assertNotContains(dashboard, 'data-employee-toggle')
        self.assertNotContains(dashboard, 'Attendance record list')
        self.assertNotContains(dashboard, '>Add attendance record</a>')
        self.assertEqual(self.client.get(reverse('hrm_pages:add-holiday')).status_code, 403)

        self.grant('employee', 'view')
        self.grant('attendance', 'view', 'add')
        page = self.client.get(reverse('hrm_pages:attendance-list'))
        self.assertContains(page, 'data-attendance-toggle aria-controls="hrm-attendance-submenu" aria-expanded="true"')
        self.assertContains(page, 'data-employee-toggle aria-controls="hrm-employee-submenu" aria-expanded="false"')
        self.assertContains(page, 'aria-current="page">Attendance record list')
        add_page = self.client.get(reverse('hrm_pages:add-attendance'))
        self.assertContains(add_page, 'aria-current="page">Add attendance record')
        self.assertContains(add_page, 'data-default="unrecorded"')
        self.assertContains(add_page, 'name="check_out" type="datetime-local"')
        self.assertContains(add_page, 'Enter times in Asia/Dhaka.')

    def test_all_pages_point_to_the_matching_create_list_and_edit_apis(self):
        payloads = {
            'attendance': {'employee': self.employee.pk, 'work_date': '2026-11-18'},
            'work-schedule': {'code': 'night', 'name': 'Night', 'working_days': [0, 6],
                              'start_time': '22:00', 'end_time': '06:00'},
            'employee-schedule-assignment': {'employee': self.employee.pk, 'schedule': self.schedule.pk,
                                             'effective_from': '2026-11-18'},
            'holiday': {'name': 'Company holiday', 'date': '2026-11-20', 'warehouse': None},
            'employee-attendance-status': {'code': 'training', 'name': 'Training'},
        }
        for resource in ATTENDANCE_RESOURCES:
            key = resource.key
            with self.subTest(resource=key):
                self.grant(key, 'view', 'add', 'change')
                page = self.client.get(reverse(f'hrm_pages:add-{key}'))
                self.assertEqual(page.status_code, 200)
                endpoint = reverse(f'hrm:{key}-list')
                self.assertContains(page, f'action="{endpoint}"')
                if resource.has_options:
                    options = reverse(f'hrm:{key}-options')
                    self.assertContains(page, f'data-options-url="{options}"')
                    self.assertEqual(self.client.get(options).status_code, 200)
                if key == 'work-schedule':
                    self.assertContains(page, 'data-number-checkbox-group="working_days"')
                    for weekday in ('Monday', 'Sunday'):
                        self.assertContains(page, weekday)
                self.assertEqual(self.client.post(endpoint, payloads[key], format='json').status_code, 403)
                csrf = self.client.cookies['csrftoken'].value
                response = self.client.post(endpoint, payloads[key], format='json', HTTP_X_CSRFTOKEN=csrf)
                self.assertEqual(response.status_code, 201, response.data)
                pk = response.data[key.replace('-', '_')]['id']
                listing = self.client.get(page.context['list_url'])
                self.assertContains(listing, f'data-api-url="{reverse(f"hrm:{key}-list-view")}"')
                rows = self.client.get(listing.context['list_api_url']).data['results']
                self.assertIn(pk, [row['id'] for row in rows])
                edit = self.client.get(reverse(f'hrm_pages:edit-{key}', args=[pk]))
                self.assertContains(edit, f'data-detail-url="{reverse(f"hrm:{key}-detail", args=[pk])}"')
                self.assertContains(edit, 'data-method="PATCH"')
                if key == 'attendance':
                    response = self.client.patch(edit.context['create_api_url'], {
                        'status': 'present', 'check_in': '2026-11-18T22:00', 'check_out': '2026-11-19T06:00',
                    }, format='json', HTTP_X_CSRFTOKEN=csrf)
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(response.data['check_out'], '2026-11-19T06:00:00+06:00')

    def test_edit_options_keep_historical_choices_without_disclosing_archived_employees(self):
        self.grant('employee-schedule-assignment', 'add', 'view')
        assignment = EmployeeScheduleAssignment.objects.create(employee=self.employee, schedule=self.schedule,
                                                                effective_from=date(2026, 11, 1))
        self.employee.delete()
        self.schedule.is_active = False
        self.schedule.save(update_fields=['is_active'])
        options = reverse('hrm:employee-schedule-assignment-options')
        edit = reverse('hrm:employee-schedule-assignment-edit-options', args=[assignment.pk])
        self.assertEqual(self.client.get(options).data, {'employee': [], 'schedule': []})
        self.assertEqual(self.client.get(edit).status_code, 403)
        self.grant('employee-schedule-assignment', 'change')
        self.assertEqual(self.client.get(edit).data, {
            'employee': [{'value': self.employee.pk, 'label': 'Archived employee (existing)'}],
            'schedule': [{'value': self.schedule.pk, 'label': self.schedule.name}],
        })
        self.assertEqual(self.client.get(reverse('hrm:attendance-options')).status_code, 403)
        self.grant('attendance', 'add')
        response = self.client.get(reverse('hrm:attendance-options'))
        self.assertEqual(response.data['employee'], [])
        self.assertIn({'value': 'unrecorded', 'label': 'Unrecorded'}, response.data['status'])
