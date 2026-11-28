from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from audit_log.models import AuditLog
from hrm.models import Department, Employee, Notice


class NoticeBoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.publisher = users.create_user(username='notice-publisher')
        cls.reader = users.create_user(username='notice-reader')
        cls.unlinked = users.create_user(username='notice-unlinked')
        cls.remover = users.create_user(username='notice-remover')
        cls.department = Department.objects.create(code='engineering', name='Engineering')
        cls.other_department = Department.objects.create(code='support', name='Support')
        cls.inactive = Department.objects.create(code='old', name='Former department', is_active=False)
        cls.employee = Employee.objects.create(employee_code='NOTICE-1', full_name='Notice Reader', user=cls.reader,
                                               department=cls.department)
        role = Group.objects.create(name='Notice publishers')
        role.permissions.add(*Permission.objects.filter(content_type__app_label='hrm', content_type__model='notice'))
        cls.publisher.groups.add(role)
        cls.grant(cls.reader, 'view_notice', 'add_notice', 'delete_notice')
        cls.grant(cls.unlinked, 'view_notice')
        cls.grant(cls.remover, 'view_notice', 'view_all_notice', 'delete_notice')
        audit_permission = Permission.objects.get(content_type__app_label='audit_log', codename='view_auditlog')
        for user in (cls.reader, cls.publisher):
            user.user_permissions.add(audit_permission)

    @staticmethod
    def grant(user, *codenames):
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='hrm', codename__in=codenames))

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.page = reverse('hrm_pages:notice-board')
        self.feed = reverse('hrm:notice-list')
        self.options = reverse('hrm:notice-options')
        self.payload = {'title': '  Office hours  ', 'message': '  First line\nSecond line  '}

    def log_in(self, user):
        self.client.force_login(user)
        self.client.credentials()

    def set_csrf(self):
        self.client.get(self.page)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)

    def notice(self, title, department=None, **kwargs):
        return Notice.objects.create(title=title, message='Notice text', department=department,
                                     created_by=self.publisher, **kwargs)

    def result_ids(self, **params):
        response = self.client.get(self.feed, params)
        self.assertEqual(response.status_code, 200)
        return [notice['id'] for notice in response.data['results']]

    def test_readers_see_only_current_department_and_company_wide_notices(self):
        global_notice = self.notice('Company-wide')
        engineering = self.notice('Engineering only', self.department)
        support = self.notice('Support only', self.other_department)
        self.assertEqual(self.client.get(self.page).status_code, 302)
        self.assertEqual(self.client.get(self.feed).status_code, 403)

        self.log_in(self.reader)
        page = self.client.get(self.page)
        self.assertContains(page, f'href="{self.page}"')
        self.assertContains(page, 'aria-current="page">Notices')
        self.assertNotContains(page, 'data-notice-form')
        self.assertNotContains(page, 'data-notice-remove>')
        self.assertEqual(self.result_ids(), [engineering.pk, global_notice.pk])
        self.assertEqual(self.result_ids(q='Support'), [])
        self.assertEqual(self.client.get(self.options).status_code, 403)
        self.set_csrf()
        self.assertEqual(self.client.post(self.feed, self.payload, format='json').status_code, 403)
        remove_url = reverse('hrm:notice-remove', args=[engineering.pk])
        self.assertEqual(self.client.delete(remove_url).status_code, 403)

        self.employee.department = self.other_department
        self.employee.save(update_fields=['department'])
        self.assertEqual(self.result_ids(), [support.pk, global_notice.pk])
        self.employee.department = None
        self.employee.save(update_fields=['department'])
        self.assertEqual(self.result_ids(), [global_notice.pk])

        self.employee.delete(actor=self.publisher.username)
        for user in (self.reader, self.unlinked):
            self.log_in(user)
            self.assertEqual(self.client.get(self.page).status_code, 403)
            self.assertEqual(self.client.get(self.feed).status_code, 403)
            self.assertNotContains(self.client.get(reverse('dashboard')), f'href="{self.page}"')

    def test_publish_validates_inputs_and_records_the_authenticated_actor(self):
        self.log_in(self.publisher)
        self.assertContains(self.client.get(self.page), 'Publish notice')
        self.assertEqual(self.client.post(self.feed, self.payload, format='json').status_code, 403)
        self.set_csrf()
        options = self.client.get(self.options).data['departments']
        self.assertEqual(options, [{'value': department.pk, 'label': department.name}
                                   for department in (self.department, self.other_department)])
        for field, invalid in (('title', ' '), ('title', 'a' * 256), ('message', ' \t '), ('message', 123),
                               ('department_id', self.inactive.pk), ('department_id', 999999)):
            with self.subTest(field=field, invalid=invalid):
                response = self.client.post(self.feed, {**self.payload, field: invalid}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
        self.assertFalse(Notice.objects.exists())

        response = self.client.post(self.feed, {**self.payload, 'department_id': self.department.pk,
                                    'created_by': self.reader.pk, 'deleted_by': self.reader.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        notice = Notice.objects.get(pk=response.data['notice']['id'])
        self.assertEqual(notice.created_by, self.publisher)
        self.assertEqual(notice.title, 'Office hours')
        self.assertEqual(notice.message, 'First line\nSecond line')
        self.assertIsNone(notice.deleted_by_id)
        self.assertEqual(response.data['notice']['audience'], self.department.name)
        self.assertEqual(AuditLog.objects.get(resource='hrm.notice', object_id=notice.pk).actor, self.publisher.username)

        response = self.client.post(self.feed, self.payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['notice']['department_id'])
        self.assertEqual(response.data['notice']['audience'], 'All employees')
        self.department.is_active = False
        self.department.save(update_fields=['is_active'])
        self.assertNotIn(self.department.pk, [row['value'] for row in self.client.get(self.options).data['departments']])
        self.log_in(self.reader)
        self.assertIn(notice.pk, self.result_ids())

    def test_pagination_filters_before_counting_and_breaks_ties_by_id(self):
        timestamp = datetime(2026, 11, 28, 4, 14, 47, tzinfo=timezone.utc)
        visible = [self.notice(f'Notice {i}', created_at=timestamp) for i in range(21)]
        hidden = self.notice('Support only', self.other_department, created_at=timestamp)
        self.log_in(self.reader)
        first = self.client.get(self.feed).data
        self.assertEqual(first['count'], 21)
        self.assertIsNotNone(first['next'])
        self.assertEqual([notice['id'] for notice in first['results']], [notice.pk for notice in reversed(visible[1:])])
        self.assertEqual(first['results'][0]['created_at_display'], '2026-11-28 10:14:47')
        self.assertEqual(self.result_ids(page=2), [visible[0].pk])
        self.log_in(self.publisher)
        self.assertEqual(self.result_ids()[0], hidden.pk)

    def test_removal_retains_history_and_audit_is_restricted(self):
        notice = self.notice('Support notice', self.other_department)
        endpoint = reverse('hrm:notice-remove', args=[notice.pk])
        self.log_in(self.remover)
        page = self.client.get(self.page)
        self.assertNotContains(page, 'data-notice-form')
        self.assertContains(page, 'data-notice-remove>')
        self.assertEqual(self.client.delete(endpoint).status_code, 403)
        self.set_csrf()
        self.assertEqual(self.client.delete(endpoint).status_code, 204)
        self.assertEqual(self.client.delete(endpoint).status_code, 404)
        self.assertEqual(self.result_ids(), [])
        notice.refresh_from_db()
        self.assertEqual(notice.deleted_by, self.remover)
        self.assertIsNotNone(notice.deleted_at)
        with self.assertRaises(ProtectedError):
            self.other_department.delete()

        audit_url = reverse('audit_log:api-list')
        self.log_in(self.publisher)
        entries = self.client.get(audit_url, {'record_type': 'Notice'}).data['results']
        self.assertEqual([(event['action'], event['actor']) for event in entries],
                         [('deleted', self.remover.username), ('created', self.publisher.username)])
        self.log_in(self.reader)
        self.assertEqual(self.client.get(audit_url, {'record_type': 'Notice'}).data['count'], 0)
