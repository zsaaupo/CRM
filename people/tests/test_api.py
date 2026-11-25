from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from people.models import Party


class PartyAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='party-operator')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        self.record = Party.objects.create(code='SUP-1', name='Existing supplier', is_supplier=True,
                                           create_by='original', modified_by='original')
        self.url = reverse('people:party-list')
        self.readonly = reverse('people:party-list-view')
        self.detail = reverse('people:party-detail', args=[self.record.pk])
        self.update = reverse('people:party-update', args=[self.record.pk])

    def grant(self, *actions):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='people',
            codename__in=[f'{action}_party' for action in actions]))

    def payload(self, **changes):
        return {'code': 'SUP-2', 'name': 'Example supplier', 'is_customer': False, 'is_supplier': True,
                'email': 'supplier@example.com', 'phone': '+8801700000000', 'address': 'Dhaka', **changes}

    def test_crud_requires_separate_action_permissions_and_protects_audit_fields(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.grant('add')
        response = self.client.post(self.url, self.payload(code='  sup-2  ', name='  Example supplier  ',
                                    id=9999, create_by='spoof', modified_by='spoof'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['message'], 'Party created successfully.')
        data = response.data['party']
        self.assertEqual(data['code'], 'SUP-2')
        self.assertEqual(data['name'], 'Example supplier')
        self.assertEqual(data['create_by'], self.user.username)
        self.assertEqual(data['modified_by'], self.user.username)
        self.assertNotEqual(data['id'], 9999)
        detail = reverse('people:party-detail', args=[data['id']])
        self.assertEqual(self.client.get(detail).status_code, 403)
        self.assertEqual(self.client.patch(detail, {'name': 'Changed'}, format='json').status_code, 403)
        self.grant('view')
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.grant('change')
        response = self.client.patch(detail, {'name': 'Changed', 'is_customer': True, 'create_by': 'spoof'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['is_customer'])
        self.assertTrue(response.data['is_supplier'])
        self.assertEqual(response.data['create_by'], self.user.username)
        self.assertEqual(response.data['modified_by'], self.user.username)
        self.assertEqual(self.client.put(detail, self.payload(code='SUP-2'), format='json').status_code, 200)
        self.assertEqual(self.client.delete(detail).status_code, 403)
        self.grant('delete')
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertFalse(Party.objects.filter(pk=data['id']).exists())
        deleted = Party.all_objects.get(pk=data['id'])
        self.assertTrue(deleted.is_deleted)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(deleted.deleted_by, self.user.username)
        self.assertEqual(self.client.get(detail).status_code, 404)

    def test_required_fields_lengths_email_website_and_duplicate_codes(self):
        self.grant('add', 'change')
        cases = [{'code': ''}, {'name': '   '}, {'code': None}, {'name': None},
                 {'email': 'invalid'}, {'website': 'invalid'}, {'phone': '1' * 33},
                 {'code': 'X' * 41}, {'name': 'N' * 201}, {'code': '  sUp-1  '}]
        for change in cases:
            with self.subTest(change=change):
                response = self.client.post(self.url, self.payload(**change), format='json')
                self.assertEqual(response.status_code, 400, response.data)
        for field in ['code', 'name']:
            payload = self.payload()
            del payload[field]
            self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.update, {'email': 'invalid'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(self.update, {'code': '  sup-1  '}, format='json').status_code, 200)
        self.record.refresh_from_db()
        self.assertEqual(self.record.code, 'SUP-1')
        self.assertEqual(self.record.email, '')

    def test_roles_validate_merged_partial_updates_and_allow_role_transfer(self):
        self.grant('add', 'change')
        for change in [{'is_supplier': False}, {'is_customer': False, 'is_supplier': False}]:
            response = self.client.post(self.url, self.payload(**change), format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('non_field_errors', response.data)
        response = self.client.patch(self.update, {'is_supplier': False}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.record.refresh_from_db()
        self.assertTrue(self.record.is_supplier)
        response = self.client.patch(self.update, {'is_customer': True, 'is_supplier': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['is_customer'])
        self.assertFalse(response.data['is_supplier'])

    def test_minimal_and_multipart_payloads_preserve_optional_defaults(self):
        self.grant('add')
        response = self.client.post(self.url, {'code': 'CUST-1', 'name': 'Customer', 'is_customer': True}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data['party']
        self.assertEqual(data['email'], '')
        self.assertEqual(data['website'], '')
        self.assertFalse(data['is_supplier'])
        self.assertTrue(data['is_active'])
        response = self.client.post(self.url, self.payload(code='BOTH', is_customer=True, contact_person='Jane',
            alternate_phone='+8801800000000', website='https://example.com', tax_number='TIN-100', city='Dhaka',
            state='Dhaka', postal_code='1200', country='Bangladesh', notes='Shared contact'), format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['party']['phone'], '+8801700000000')
        self.assertTrue(response.data['party']['is_customer'])
        self.assertTrue(response.data['party']['is_supplier'])

    def test_lists_search_role_filters_ordering_and_pagination(self):
        self.grant('view')
        customer = Party.objects.create(code='CUST-1', name='Alpha customer', is_customer=True, email='search@example.com')
        Party.objects.create(code='BOTH', name='Both roles', is_customer=True, is_supplier=True, is_active=False)
        self.assertEqual(self.client.get(self.url).data['count'], 3)
        self.assertEqual(self.client.get(self.readonly, {'is_supplier': 'true'}).data['count'], 2)
        self.assertEqual(self.client.get(self.url, {'is_customer': 'true'}).data['count'], 2)
        self.assertEqual(self.client.get(self.url, {'is_customer': 'true', 'is_active': 'true'}).data['count'], 1)
        self.assertEqual(self.client.get(self.url, {'is_supplier': 'false'}).data['results'][0]['id'], customer.pk)
        self.assertEqual(self.client.get(self.url, {'q': 'SEARCH@EXAMPLE'}).data['count'], 1)
        self.assertEqual(self.client.get(self.url, {'q': '  sup-1  '}).data['results'][0]['id'], self.record.pk)
        for name in ['is_supplier', 'is_customer', 'is_active']:
            self.assertEqual(self.client.get(self.url, {name: 'invalid'}).status_code, 400)
        Party.objects.bulk_create([Party(code=f'EXTRA-{i}', name=f'Extra {i:02}', is_customer=True) for i in range(21)])
        first = self.client.get(self.url).data
        self.assertEqual(first['count'], 24)
        self.assertEqual(len(first['results']), 20)
        self.assertEqual(first['results'][0]['id'], customer.pk)
        self.assertEqual(len(self.client.get(first['next']).data['results']), 4)
        self.assertEqual(self.client.get(self.url, {'page': 999}).status_code, 404)

    def test_update_endpoint_needs_only_change_and_preserves_creator(self):
        self.grant('change')
        self.assertEqual(self.client.get(self.detail).status_code, 403)
        response = self.client.patch(self.update, {'is_active': False, 'email': ''}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['is_active'])
        self.assertEqual(response.data['create_by'], 'original')
        self.assertEqual(response.data['modified_by'], self.user.username)
        self.assertEqual(self.client.put(self.update, self.payload(code='SUP-1'), format='json').status_code, 200)
        self.assertEqual(self.client.options(self.update).status_code, 200)
        for method in ['get', 'post', 'delete', 'head']:
            self.assertEqual(getattr(self.client, method)(self.update).status_code, 405)

    def test_csrf_is_required_for_all_writes_and_missing_records_return_404(self):
        self.grant('add', 'change', 'delete', 'view')
        self.client.credentials()
        for method, url in [('post', self.url), ('put', self.detail), ('patch', self.detail),
                            ('put', self.update), ('patch', self.update), ('delete', self.detail)]:
            self.assertEqual(getattr(self.client, method)(url, self.payload(), format='json').status_code, 403)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        missing = reverse('people:party-detail', args=[999999])
        for method in ['get', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(missing, self.payload()).status_code, 404)

    def test_group_permissions_revocation_and_inactive_or_anonymous_users(self):
        self.assertEqual(APIClient().get(self.url).status_code, 403)
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        role = Group.objects.create(name='Party readers')
        role.permissions.add(Permission.objects.get(content_type__app_label='people', codename='view_party'))
        self.user.groups.add(role)
        self.assertEqual(self.client.get(self.readonly).status_code, 200)
        role.permissions.clear()
        self.assertEqual(self.client.get(self.readonly).status_code, 403)
        self.grant('view')
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_read_only_methods_never_cache_and_database_failure_rolls_back(self):
        self.grant('view', 'add')
        for url in [self.url, self.readonly, self.detail]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(self.client.head(url).status_code, 200)
            self.assertEqual(self.client.options(url).status_code, 200)
        for method in ['post', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(self.readonly, self.payload(), format='json').status_code, 405)
        with patch('people.serializers.PartySerializer.save', side_effect=IntegrityError):
            response = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Party.objects.count(), 1)
