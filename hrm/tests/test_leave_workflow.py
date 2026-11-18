from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from hrm.models import Employee, LeaveRequest, LeaveType


class LeaveWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.applicant = users.create_user(username='applicant')
        cls.reviewer = users.create_user(username='reviewer')
        cls.employee = Employee.objects.create(employee_code='LEAVE-1', full_name='Applicant', user=cls.applicant)
        cls.other = Employee.objects.create(employee_code='LEAVE-2', full_name='Other Employee')
        cls.leave_type = LeaveType.objects.create(code='annual', name='Annual leave', is_paid=True)
        cls.inactive_type = LeaveType.objects.create(code='old', name='Inactive leave', is_paid=False, is_active=False)
        cls.grant(cls.applicant, 'view_leaverequest', 'add_leaverequest')
        cls.grant(cls.reviewer, 'view_leaverequest', 'review_leaverequest', 'add_leaverequest')

    @staticmethod
    def grant(user, *codenames):
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='hrm', codename__in=codenames))

    def client_for(self, user, *, csrf=True):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        if csrf:
            token = client.get(reverse('account:login')).data['csrfToken']
            client.credentials(HTTP_X_CSRFTOKEN=token)
        return client

    def leave(self, employee, start=18, end=20):
        return LeaveRequest.objects.create(employee=employee, leave_type=self.leave_type,
                                            start_date=date(2026, 11, start), end_date=date(2026, 11, end))

    def payload(self, **changes):
        return {'leave_type': self.leave_type.pk, 'start_date': '2026-11-18', 'end_date': '2026-11-20', **changes}

    def test_apply_page_and_api_use_the_linked_employee_and_hide_other_requests(self):
        client = self.client_for(self.applicant, csrf=False)
        foreign = self.leave(self.other)
        page = client.get(reverse('hrm_pages:add-leave-request'))
        self.assertContains(page, 'data-leave-toggle aria-controls="hrm-leave-submenu" aria-expanded="true"')
        self.assertContains(page, 'aria-current="page">Apply for leave')
        self.assertContains(page, '>Request list</a>')
        self.assertNotContains(page, '>Leave type list</a>')
        self.assertNotContains(page, 'name="employee"')
        options = client.get(page.context['options_api_url']).data
        self.assertEqual(options, {'leave_type': [{'value': self.leave_type.pk, 'label': 'Annual leave (Paid)'}]})
        endpoint = page.context['create_api_url']
        self.assertEqual(client.post(endpoint, self.payload(), format='json').status_code, 403)
        client.credentials(HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        spoof = self.payload(employee=self.other.pk)
        self.assertEqual(client.post(endpoint, spoof, format='json').status_code, 403)
        response = client.post(endpoint, self.payload(status='approved', reviewed_by=self.reviewer.pk, reason='Trip'),
                               format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = response.data['leave_request']
        self.assertEqual(record['employee'], self.employee.pk)
        self.assertEqual(record['status'], 'pending')
        self.assertIsNone(record['reviewed_by'])
        listing = client.get(page.context['list_url'])
        rows = client.get(listing.context['list_api_url']).data['results']
        self.assertEqual([row['id'] for row in rows], [record['id']])
        self.assertContains(client.get(reverse('hrm_pages:leave-request-detail', args=[record['id']])), 'Trip')
        for name in ['hrm:leave-request-detail', 'hrm_pages:leave-request-detail']:
            self.assertEqual(client.get(reverse(name, args=[foreign.pk])).status_code, 404)
        self.assertEqual(client.post(reverse('hrm:leave-request-review', args=[record['id']]),
                                     {'status': 'approved'}, format='json').status_code, 403)

    def test_reviewer_can_review_pending_requests_and_cannot_approve_an_overlap(self):
        client = self.client_for(self.reviewer)
        first = self.leave(self.employee)
        second = self.leave(self.employee, 20, 21)
        page = client.get(reverse('hrm_pages:leave-request-detail', args=[first.pk]))
        self.assertContains(page, 'Approve request')
        self.assertContains(page, 'Reject request')
        endpoint = page.context['review_api_url']
        response = client.post(endpoint, {'status': 'approved', 'reviewed_by': self.applicant.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status_name'], 'Approved')
        self.assertEqual(response.data['reviewed_by'], self.reviewer.pk)
        self.assertIsNotNone(response.data['reviewed_at'])
        self.assertEqual(client.post(endpoint, {'status': 'rejected'}, format='json').status_code, 409)
        self.assertNotContains(client.get(reverse('hrm_pages:leave-request-detail', args=[first.pk])), 'Approve request')
        second_review = reverse('hrm:leave-request-review', args=[second.pk])
        response = client.post(second_review, {'status': 'approved'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('start_date', response.data)
        second.refresh_from_db()
        self.assertEqual(second.status, LeaveRequest.Status.PENDING)
        self.assertEqual(client.post(second_review, {'status': 'rejected'}, format='json').status_code, 200)
        page = client.get(reverse('hrm_pages:leave-request-list'))
        results = client.get(page.context['list_api_url'], {'status': 'approved'}).data['results']
        self.assertEqual([row['id'] for row in results], [first.pk])

    def test_reviewer_can_submit_for_another_employee_and_cannot_review_own_request(self):
        self.other.user = self.reviewer
        self.other.save(update_fields=['user'])
        client = self.client_for(self.reviewer)
        page = client.get(reverse('hrm_pages:add-leave-request'))
        self.assertContains(page, 'name="employee"')
        choices = client.get(page.context['options_api_url']).data['employee']
        self.assertEqual({item['value'] for item in choices}, {self.employee.pk, self.other.pk})
        response = client.post(page.context['create_api_url'], self.payload(employee=self.employee.pk), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        own = self.leave(self.other)
        self.assertEqual(client.post(reverse('hrm:leave-request-review', args=[own.pk]),
                                     {'status': 'approved'}, format='json').status_code, 403)

    def test_leave_type_management_and_invalid_applications(self):
        self.grant(self.reviewer, 'view_leavetype', 'add_leavetype', 'change_leavetype', 'delete_leavetype')
        admin = self.client_for(self.reviewer)
        page = admin.get(reverse('hrm_pages:leave-request-list'))
        self.assertContains(page, 'Manage leave types')
        self.assertContains(page, '>Leave type list</a>')
        type_form = admin.get(reverse('hrm_pages:add-leave-type'))
        response = admin.post(type_form.context['create_api_url'],
                              {'code': 'personal', 'name': 'Personal leave', 'is_paid': False}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['leave_type']['is_paid'])
        client = self.client_for(self.applicant)
        endpoint = reverse('hrm:leave-request-list')
        for payload, field in [(self.payload(leave_type=self.inactive_type.pk), 'leave_type'),
                               (self.payload(end_date='2026-11-17'), 'end_date')]:
            response = client.post(endpoint, payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn(field, response.data)
        self.employee.delete()
        self.assertEqual(client.get(reverse('hrm_pages:add-leave-request')).status_code, 403)
        self.assertEqual(client.post(endpoint, self.payload(), format='json').status_code, 403)
