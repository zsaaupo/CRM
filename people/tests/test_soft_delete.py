from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from people.admin import PartyAdmin
from people.models import Party


class PartySoftDeleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='party-admin', password=None)
        self.party = Party.objects.create(code='SUP-1', name='Original supplier', is_supplier=True,
                                           create_by='original', modified_by='original')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        self.detail = reverse('people:party-detail', args=[self.party.pk])

    def test_api_delete_preserves_row_and_history_and_hides_all_access_paths(self):
        with patch('django.db.models.deletion.Collector.delete', side_effect=AssertionError('Hard deletion called')):
            self.assertEqual(self.client.delete(self.detail).status_code, 204)
        deleted = Party.all_objects.get(pk=self.party.pk)
        self.assertTrue(deleted.is_deleted)
        self.assertEqual(deleted.deleted_by, self.user.username)
        self.assertEqual(deleted.modified_by, self.user.username)
        self.assertEqual(deleted.create_by, 'original')
        self.assertEqual(deleted.created_at, self.party.created_at)
        self.assertEqual(deleted.name, 'Original supplier')
        self.assertIsNotNone(deleted.deleted_at)
        self.assertFalse(Party.objects.filter(pk=deleted.pk).exists())
        for route in ['party-list', 'party-list-view']:
            for filters in [{}, {'q': 'SUP-1'}, {'is_supplier': 'true'}, {'include_deleted': 'true'}]:
                response = self.client.get(reverse(f'people:{route}'), filters)
                self.assertEqual(response.data['count'], 0)
                self.assertEqual(response.data['results'], [])
        for method in ['get', 'head', 'put', 'patch', 'delete']:
            response = getattr(self.client, method)(self.detail, {'is_deleted': False, 'name': 'Restored'}, format='json')
            self.assertEqual(response.status_code, 404)
        update = reverse('people:party-update', args=[deleted.pk])
        for method in ['put', 'patch']:
            self.assertEqual(getattr(self.client, method)(update, {'name': 'Restored'}, format='json').status_code, 404)
        latest = Party.all_objects.get(pk=deleted.pk)
        self.assertEqual(latest.deleted_at, deleted.deleted_at)
        self.assertEqual(latest.updated_at, deleted.updated_at)
        self.assertEqual(latest.name, 'Original supplier')

    def test_deletion_fields_cannot_be_written_and_inactive_is_not_deleted(self):
        payload = {'code': 'CUST-1', 'name': 'Inactive customer', 'is_customer': True, 'is_active': False,
                   'is_deleted': True, 'deleted_at': '2026-01-01T00:00:00Z', 'deleted_by': 'spoof'}
        response = self.client.post(reverse('people:party-list'), payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data['party']
        self.assertFalse(data['is_deleted'])
        self.assertIsNone(data['deleted_at'])
        self.assertIsNone(data['deleted_by'])
        self.assertEqual(self.client.get(reverse('people:party-detail', args=[data['id']])).status_code, 200)
        inactive = self.client.get(reverse('people:party-list'), {'is_active': 'false'}).data
        self.assertEqual(inactive['count'], 1)
        response = self.client.patch(self.detail, {'is_deleted': True, 'deleted_by': 'spoof'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_deleted'])

    def test_deleted_codes_remain_reserved_for_creates_updates_and_model_validation(self):
        self.party.delete(actor='original-deleter')
        for code in ['SUP-1', 'sup-1', '  sUp-1  ']:
            response = self.client.post(reverse('people:party-list'),
                {'code': code, 'name': 'Replacement', 'is_supplier': True}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('code', response.data)
        other = Party.objects.create(code='OTHER', name='Other', is_customer=True)
        response = self.client.patch(reverse('people:party-update', args=[other.pk]),
                                     {'code': 'sup-1'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        candidate = Party(code='sup-1', name='Replacement', is_supplier=True)
        with self.assertRaises(ValidationError):
            candidate.full_clean(exclude=['create_by', 'modified_by'])
        self.assertEqual(Party.all_objects.count(), 2)

    def test_instance_and_bulk_deletion_are_soft_and_idempotent(self):
        pk = self.party.pk
        with patch('django.db.models.deletion.Collector.delete', side_effect=AssertionError('Hard deletion called')):
            self.assertEqual(self.party.delete(actor='first'), (1, {'people.Party': 1}))
            self.assertTrue(self.party.is_deleted)
            first_deleted_at = self.party.deleted_at
            self.assertEqual(self.party.pk, pk)
            self.assertEqual(self.party.delete(actor='second'), (0, {'people.Party': 0}))
            second = Party.objects.create(code='SECOND', name='Second', is_customer=True)
            count, labels = Party.all_objects.filter(pk__in=[pk, second.pk]).delete(actor='bulk')
        self.assertEqual(count, 1)
        self.assertEqual(labels, {'people.Party': 1})
        self.assertEqual(Party.all_objects.count(), 2)
        self.assertEqual(Party.objects.count(), 0)
        self.party.refresh_from_db()
        self.assertEqual(self.party.deleted_at, first_deleted_at)
        self.assertEqual(self.party.deleted_by, 'first')
        self.assertEqual(Party.all_objects.get(pk=second.pk).deleted_by, 'bulk')
        with self.assertRaises(ValueError):
            Party(code='UNSAVED', name='Unsaved', is_supplier=True).delete()

    def test_default_bulk_delete_hides_rows_and_preserves_actor_when_unspecified(self):
        count, labels = Party.objects.all().delete()
        self.assertEqual(count, 1)
        self.assertEqual(labels, {'people.Party': 1})
        self.assertEqual(Party.objects.count(), 0)
        self.assertEqual(Party.all_objects.count(), 1)
        self.party.refresh_from_db()
        self.assertTrue(self.party.is_deleted)
        self.assertIsNone(self.party.deleted_by)
        self.assertEqual(self.party.modified_by, 'original')

    def test_admin_single_and_bulk_delete_preserve_rows_and_record_actor(self):
        model_admin = PartyAdmin(Party, admin.site)
        request = RequestFactory().post('/admin/people/party/')
        request.user = self.user
        second = Party.objects.create(code='SECOND', name='Second', is_customer=True)
        with patch('django.db.models.deletion.Collector.delete', side_effect=AssertionError('Hard deletion called')):
            model_admin.delete_model(request, self.party)
            model_admin.delete_queryset(request, Party.objects.filter(pk=second.pk))
        self.assertEqual(Party.all_objects.count(), 2)
        self.assertEqual(model_admin.get_queryset(request).count(), 2)
        self.assertTrue(all(party.deleted_by == self.user.username for party in Party.all_objects.all()))
