from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from people.models import Lead


class LeadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.operator = users.create_user(username='lead-operator')
        cls.assignee = users.create_user(username='lead-owner')
        cls.inactive_user = users.create_user(username='inactive-owner', is_active=False)
        cls.role = Group.objects.create(name='Lead operators')
        cls.operator.groups.add(cls.role)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.operator)
        self.list_url = reverse('people_pages:lead-list')
        self.add_url = reverse('people_pages:add-lead')
        self.api_url = reverse('people:lead-list')
        self.options_url = reverse('people:lead-options')

    def grant(self, *actions):
        self.role.permissions.add(*Permission.objects.filter(
            content_type__app_label='people', codename__in=[f'{action}_lead' for action in actions],
        ))

    def enable_csrf(self):
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def test_sidebar_and_pages_follow_lead_permissions(self):
        self.assertRedirects(APIClient().get(self.list_url), reverse('account_pages:login'))
        self.assertNotContains(self.client.get(reverse('dashboard')), 'data-lead-toggle')
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(self.client.get(self.api_url).status_code, 403)
        self.grant('view')
        listing = self.client.get(self.list_url)
        self.assertContains(listing, 'aria-current="page">Lead list')
        self.assertNotContains(listing, 'data-people-toggle')
        self.assertNotContains(listing, f'href="{self.add_url}"')
        self.assertEqual(self.client.get(self.add_url).status_code, 403)
        self.assertEqual(self.client.get(self.options_url).status_code, 403)
        self.enable_csrf()
        self.assertEqual(self.client.post(self.api_url, {'name': 'Restricted'}, format='json').status_code, 403)
        self.grant('add')
        form = self.client.get(self.add_url)
        self.assertContains(form, 'aria-current="page">Add lead')
        self.assertContains(form, f'action="{self.api_url}"')
        self.assertContains(form, f'data-options-url="{self.options_url}"')
        self.assertContains(form, f'data-success-url="{self.list_url}"')
        fields = ('name', 'company', 'job_title', 'email', 'phone', 'source', 'status', 'expected_value', 'assigned_to',
                  'follow_up_date', 'expected_close_date', 'notes')
        for field in fields:
            self.assertContains(form, f'name="{field}"')
        self.assertContains(form, 'data-options-field="status" data-default="new"')
        self.assertContains(form, 'type="date" class="form-input"', count=2)
        self.assertEqual(form.context['create_api_url'], self.api_url)

    def test_create_search_view_and_edit_lead(self):
        self.grant('view', 'add', 'change')
        options = self.client.get(self.options_url).data
        self.assertEqual([row['label'] for row in options['status']],
                         ['New', 'Contacted', 'Qualified', 'Proposal', 'Converted', 'Lost'])
        self.assertEqual({row['value'] for row in options['assigned_to']}, {self.operator.pk, self.assignee.pk})
        payload = {
            'name': 'Alex Karim', 'company': 'Acme Ltd', 'job_title': 'Buyer', 'email': 'alex@example.com',
            'phone': '+8801700000000', 'source': 'Referral', 'status': 'qualified', 'expected_value': '2500.75',
            'assigned_to': self.assignee.pk, 'follow_up_date': '2026-12-01', 'expected_close_date': '2026-12-15',
            'notes': 'Send proposal after the call.',
        }
        self.assertEqual(self.client.post(self.api_url, payload, format='json').status_code, 403)
        self.enable_csrf()
        response = self.client.post(self.api_url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = response.data['lead']
        for field, value in payload.items():
            self.assertEqual(record[field], value)
        self.assertEqual(record['status_label'], 'Qualified')
        self.assertEqual(record['assigned_to_name'], self.assignee.username)
        lead = Lead.objects.get(pk=record['id'])
        self.assertEqual(lead.expected_value, Decimal('2500.75'))
        self.assertEqual(lead.create_by, self.operator.username)

        listing = self.client.get(self.list_url)
        self.assertContains(listing, 'data-edit-url-template="/people/leads/0/edit/"')
        results = self.client.get(listing.context['list_api_url'], {'q': 'acme'}).data['results']
        self.assertEqual([row['id'] for row in results], [lead.pk])
        detail_url = reverse('people:lead-detail', args=[lead.pk])
        detail = self.client.get(reverse('people_pages:lead-detail', args=[lead.pk]))
        self.assertContains(detail, f'data-api-url="{detail_url}"')
        self.assertContains(detail, 'data-detail-field="notes"')
        edit = self.client.get(reverse('people_pages:edit-lead', args=[lead.pk]))
        self.assertContains(edit, 'data-method="PATCH"')
        response = self.client.patch(edit.context['create_api_url'], {
            'status': 'proposal', 'assigned_to': None, 'follow_up_date': None,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        updated = self.client.get(detail_url).data
        self.assertEqual(updated['status_label'], 'Proposal')
        self.assertIsNone(updated['assigned_to_name'])
        self.assertIsNone(updated['follow_up_date'])
        self.assertEqual(updated['notes'], payload['notes'])

    def test_optional_fields_and_retaining_an_existing_inactive_assignee(self):
        self.grant('view', 'add', 'change')
        self.enable_csrf()
        response = self.client.post(self.api_url, {'name': 'Only name'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['lead']['status'], Lead.Status.NEW)
        self.assertIsNone(response.data['lead']['expected_value'])

        invalid = self.client.post(self.api_url, {
            'name': ' ', 'status': 'unknown', 'assigned_to': self.inactive_user.pk, 'expected_value': '-1',
        }, format='json')
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(set(invalid.data), {'name', 'status', 'assigned_to', 'expected_value'})

        existing = Lead.objects.create(name='Existing lead', assigned_to=self.inactive_user)
        options = self.client.get(reverse('people:lead-edit-options', args=[existing.pk])).data
        self.assertIn({'value': self.inactive_user.pk, 'label': 'inactive-owner (inactive)'}, options['assigned_to'])
        saved = self.client.patch(reverse('people:lead-detail', args=[existing.pk]), {
            'status': 'contacted', 'assigned_to': self.inactive_user.pk,
        }, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
