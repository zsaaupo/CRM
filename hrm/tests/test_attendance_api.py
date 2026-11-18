from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from hrm.models import Attendance, Employee, EmployeeAttendanceStatus, EmployeeScheduleAssignment, Holiday, WorkSchedule
from warehouse.models import WareHouse


@override_settings(TIME_ZONE='Asia/Dhaka')
class AttendanceAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = Employee.objects.create(employee_code='ATT-01', full_name='Test Employee')
        cls.schedule = WorkSchedule.objects.create(code='office', name='Office', working_days=[0, 1, 2, 3, 6],
                                                   start_time=time(9), end_time=time(18))
        cls.warehouse = WareHouse.objects.create(code='DHK', name='Dhaka', address='Dhaka', phone='123')
        cls.user = get_user_model().objects.create_user(username='attendance-operator')
        cls.role = Group.objects.create(name='Attendance operators')
        cls.user.groups.add(cls.role)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def grant(self, model, *actions):
        self.role.permissions.add(*Permission.objects.filter(content_type__app_label='hrm',
            codename__in=[f'{action}_{model._meta.model_name}' for action in actions]))

    def test_crud_routes_require_model_permissions_and_record_audit(self):
        resources = [
            (WorkSchedule, 'work-schedule', 'work_schedule',
             {'code': 'night', 'name': 'Night', 'working_days': [0, 6], 'start_time': '22:00', 'end_time': '06:00'},
             {'grace_minutes': 10}),
            (EmployeeScheduleAssignment, 'employee-schedule-assignment', 'employee_schedule_assignment',
             {'employee': self.employee.pk, 'schedule': self.schedule.pk, 'effective_from': '2026-11-01'},
             {'effective_to': '2026-11-30'}),
            (Holiday, 'holiday', 'holiday', {'name': 'Company holiday', 'date': '2026-11-20'}, {'name': 'Updated'}),
            (EmployeeAttendanceStatus, 'employee-attendance-status', 'employee_attendance_status',
             {'code': 'training', 'name': 'Training'}, {'name': 'Training day'}),
            (Attendance, 'attendance', 'attendance', {'employee': self.employee.pk}, {'notes': 'Reviewed'}),
        ]
        for model, resource, key, payload, changes in resources:
            with self.subTest(resource=resource):
                url = reverse(f'hrm:{resource}-list')
                listing = reverse(f'hrm:{resource}-list-view')
                self.assertEqual(self.client.get(listing).status_code, 403)
                self.assertEqual(self.client.post(url, payload, format='json').status_code, 403)
                self.grant(model, 'add')
                response = self.client.post(url, {**payload, 'create_by': 'spoofed'}, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                record = response.data[key]
                self.assertEqual(record['create_by'], self.user.username)
                self.assertEqual(record['modified_by'], self.user.username)
                if model is Attendance:
                    self.assertEqual(record['status'], 'unrecorded')
                    self.assertEqual(record['status_name'], 'Unrecorded')
                detail = reverse(f'hrm:{resource}-detail', args=[record['id']])
                update = reverse(f'hrm:{resource}-update', args=[record['id']])
                self.assertEqual(self.client.get(detail).status_code, 403)
                self.assertEqual(self.client.patch(update, changes, format='json').status_code, 403)
                self.assertEqual(self.client.delete(detail).status_code, 403)
                self.grant(model, 'view', 'change', 'delete')
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.get(listing).status_code, 200)
                self.assertEqual(self.client.get(detail).status_code, 200)
                response = self.client.patch(update, changes, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                for field, value in changes.items():
                    self.assertEqual(response.data[field], value)
                self.assertEqual(self.client.delete(detail).status_code, 204)
                self.assertEqual(self.client.get(detail).status_code, 404)

    def test_assignments_reject_overlap_and_filter_by_period(self):
        self.grant(EmployeeScheduleAssignment, 'add', 'view', 'change')
        url = reverse('hrm:employee-schedule-assignment-list')
        payload = {'employee': self.employee.pk, 'schedule': self.schedule.pk}
        for start, end in [('2026-11-01', '2026-11-10'), ('2026-11-11', None)]:
            response = self.client.post(url, {**payload, 'effective_from': start, 'effective_to': end}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(url, {**payload, 'effective_from': '2026-11-10'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('effective_from', response.data)
        pk = EmployeeScheduleAssignment.objects.get(effective_to__isnull=False).pk
        response = self.client.patch(reverse('hrm:employee-schedule-assignment-detail', args=[pk]),
                                     {'effective_to': None}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.get(url, {'from_date': '2026-11-11', 'to_date': '2026-11-11', 'q': 'office'})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['effective_from'], '2026-11-11')

    def test_attendance_validates_merged_changes_and_keeps_archived_history(self):
        self.grant(Attendance, 'add', 'view', 'change')
        url = reverse('hrm:attendance-list')
        payload = {'employee': self.employee.pk, 'work_date': '2026-11-18', 'status': 'present',
                   'check_in': '2026-11-18T22:00:00+06:00'}
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        detail = reverse('hrm:attendance-detail', args=[response.data['attendance']['id']])
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 400)
        response = self.client.patch(detail, {'check_out': '2026-11-18T21:00:00+06:00'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('check_out', response.data)
        response = self.client.patch(detail, {'check_out': '2026-11-19T06:00:00+06:00'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch(detail, {'status': 'absent'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('status', response.data)
        self.employee.delete()
        response = self.client.patch(detail, {'notes': 'Archived employee history'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['employee_name'])
        response = self.client.post(url, {'employee': self.employee.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.get(url, {'employee': self.employee.pk, 'status': 'present', 'from_date': '2026-11-18'})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(self.client.get(url, {'from_date': '2026-11-19', 'to_date': '2026-11-18'}).status_code, 400)

    def test_holidays_are_unique_per_scope_and_default_status_is_protected(self):
        self.grant(Holiday, 'add', 'view')
        self.grant(EmployeeAttendanceStatus, 'delete')
        url = reverse('hrm:holiday-list')
        payload = {'date': '2026-11-20', 'name': 'Holiday'}
        for scope in [{}, {'warehouse': self.warehouse.pk}]:
            response = self.client.post(url, {**payload, **scope}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(self.client.post(url, {**payload, **scope}, format='json').status_code, 400)
        self.assertEqual(Holiday.objects.filter(date=date(2026, 11, 20)).count(), 2)
        self.assertEqual(self.client.get(url, {'warehouse': self.warehouse.pk}).data['count'], 1)
        status = EmployeeAttendanceStatus.objects.get(code='unrecorded')
        detail = reverse('hrm:employee-attendance-status-detail', args=[status.pk])
        self.assertEqual(self.client.delete(detail).status_code, 409)
