from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from warehouse.models import WareHouse


class WareHouseAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='warehouse-operator')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)
        self.url = reverse('warehouse:warehouse-list')
        self.record = WareHouse.objects.create(name='Central depot', address='Dhaka', phone='01700000000',
                                               code='MAIN', create_by='original', modified_by='original')
        self.detail = reverse('warehouse:warehouse-detail', args=[self.record.pk])
        self.update = reverse('warehouse:warehouse-update', args=[self.record.pk])
        self.readonly = reverse('warehouse:warehouse-list-view')

    def permission(self, action):
        return Permission.objects.get(content_type__app_label='warehouse', codename=f'{action}_warehouse')

    def grant(self, *actions):
        self.user.user_permissions.add(*(self.permission(action) for action in actions))

    def payload(self, **changes):
        return {'name': 'North depot', 'address': 'Gazipur', 'phone': '01800000000',
                'email': 'north@example.com', 'code': 'NORTH', 'is_active': True, **changes}

    def test_crud_uses_action_permissions_and_protects_audit_fields(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.grant('add')
        response = self.client.post(self.url, self.payload(id=9999, create_by='spoof', modified_by='spoof'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn('message', response.data)
        created = response.data['warehouse']
        self.assertNotEqual(created['id'], 9999)
        self.assertEqual(created['create_by'], self.user.username)
        self.assertEqual(created['modified_by'], self.user.username)
        detail = reverse('warehouse:warehouse-detail', args=[created['id']])
        self.assertEqual(self.client.get(detail).status_code, 403)
        self.assertEqual(self.client.patch(detail, {'name': 'Updated'}, format='json').status_code, 403)
        self.grant('view')
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.get(self.url).data['count'], 2)
        self.grant('change')
        response = self.client.patch(detail, {'name': 'Updated', 'create_by': 'spoof', 'modified_by': 'spoof'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['name'], 'Updated')
        self.assertEqual(response.data['create_by'], self.user.username)
        self.assertEqual(response.data['modified_by'], self.user.username)
        self.assertEqual(self.client.put(detail, self.payload(name='Replaced'), format='json').status_code, 200)
        self.assertEqual(self.client.delete(detail).status_code, 403)
        self.grant('delete')
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertFalse(WareHouse.objects.filter(pk=created['id']).exists())
        self.assertEqual(self.client.get(detail).status_code, 404)

    def test_update_endpoint_requires_only_change_and_supports_put_patch(self):
        self.grant('view')
        self.assertEqual(self.client.patch(self.update, {'name': 'Denied'}, format='json').status_code, 403)
        self.user.user_permissions.clear()
        self.grant('change')
        response = self.client.patch(self.update, {'is_active': False, 'email': None, 'code': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['is_active'])
        self.assertIsNone(response.data['email'])
        self.assertIsNone(response.data['code'])
        self.assertEqual(response.data['create_by'], 'original')
        self.assertEqual(response.data['modified_by'], self.user.username)
        self.assertEqual(self.client.put(self.update, self.payload(), format='json').status_code, 200)
        for method in ['get', 'post', 'delete', 'head']:
            self.assertEqual(getattr(self.client, method)(self.update).status_code, 405)
        self.assertEqual(self.client.options(self.update).status_code, 200)

    def test_required_fields_lengths_email_and_partial_update_validation(self):
        self.grant('add', 'change')
        cases = [{'name': ''}, {'name': None}, {'address': ''}, {'phone': ''},
                 {'email': 'not-an-email'}, {'name': 'N' * 201}, {'address': 'A' * 201}]
        for invalid in cases:
            with self.subTest(payload=invalid):
                response = self.client.post(self.url, self.payload(**invalid), format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self.client.patch(self.update, invalid, format='json').status_code, 400)
        for field in ['name', 'address', 'phone']:
            data = self.payload()
            del data[field]
            self.assertEqual(self.client.post(self.url, data, format='json').status_code, 400)
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, 'Central depot')

    def test_optional_values_and_multipart_input_match_model(self):
        self.grant('add')
        data = {'name': '  Optional depot  ', 'address': 'Dhaka', 'phone': '01700000000'}
        response = self.client.post(self.url, data, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        warehouse = response.data['warehouse']
        self.assertEqual(warehouse['name'], 'Optional depot')
        self.assertIsNone(warehouse['email'])
        self.assertIsNone(warehouse['code'])
        self.assertEqual(str(WareHouse.objects.get(pk=warehouse['id'])), 'Optional depot')
        response = self.client.post(self.url, self.payload(email='', code=''), format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_session_authentication_and_csrf_are_required(self):
        self.grant('view', 'add', 'change', 'delete')
        anonymous = APIClient()
        for url in [self.url, self.readonly, self.detail]:
            self.assertEqual(anonymous.get(url).status_code, 403)
        for method, url, data in [('post', self.url, self.payload()), ('put', self.detail, self.payload()),
                                  ('patch', self.detail, {'name': 'Changed'}), ('patch', self.update, {'name': 'Changed'}),
                                  ('delete', self.detail, {})]:
            with self.subTest(method=method, url=url):
                self.client.credentials()
                self.assertEqual(getattr(self.client, method)(url, data, format='json').status_code, 403)
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, 'Central depot')

    def test_search_pagination_ordering_and_read_only_list(self):
        self.grant('view')
        WareHouse.objects.bulk_create([WareHouse(name=f'Depot {index:02}', address='City', phone='123', code=f'D{index}')
                                      for index in range(22)])
        response = self.client.get(self.readonly)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 23)
        self.assertEqual(len(response.data['results']), 20)
        self.assertEqual(response.data['results'][0]['id'], self.record.pk)
        self.assertEqual(len(self.client.get(response.data['next']).data['results']), 3)
        self.assertEqual(self.client.get(self.readonly, {'q': 'main'}).data['count'], 1)
        self.assertEqual(self.client.get(self.readonly, {'q': 'does not exist'}).data['results'], [])
        self.assertEqual(self.client.get(self.readonly, {'page': 99}).status_code, 404)
        listing = self.client.get(self.url).data
        self.assertEqual(response.data['count'], listing['count'])
        self.assertEqual(response.data['results'], listing['results'])
        self.assertEqual(self.client.head(self.readonly).status_code, 200)
        self.assertEqual(self.client.options(self.readonly).status_code, 200)
        for method in ['post', 'put', 'patch', 'delete']:
            self.assertEqual(getattr(self.client, method)(self.readonly, {}, format='json').status_code, 405)

    def test_group_permission_revocation_and_inactive_users(self):
        group = Group.objects.create(name='Warehouse operators')
        group.permissions.add(self.permission('view'), self.permission('change'))
        self.user.groups.add(group)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.patch(self.update, {'name': 'Edited'}, format='json').status_code, 200)
        group.permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.patch(self.update, {'name': 'Denied'}, format='json').status_code, 403)
        self.grant('view', 'change')
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.patch(self.update, {'name': 'Denied'}, format='json').status_code, 403)

    def test_no_cache_headers_and_missing_records(self):
        self.grant('view', 'add', 'change', 'delete')
        for url in [self.url, self.readonly, self.detail]:
            self.assertIn('no-store', self.client.get(url)['Cache-Control'])
        for route in ['warehouse-detail', 'warehouse-update']:
            url = reverse(f'warehouse:{route}', args=[999999])
            self.assertEqual(self.client.patch(url, {'name': 'Missing'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(reverse('warehouse:warehouse-detail', args=[999999])).status_code, 404)

    def test_save_integrity_errors_return_validation_error_without_mutation(self):
        self.grant('change')
        with patch('warehouse.serializers.WareHouseSerializer.save', side_effect=IntegrityError):
            response = self.client.patch(self.update, {'name': 'Failed save'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('non_field_errors', response.data)
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, 'Central depot')
