from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, OperationalError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied

from warehouse.models import RoleWarehouseAccess, WareHouse
from warehouse.serializers import RoleWarehouseAccessSerializer


class RoleWarehouseAccessAPITests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='assignment-manager')
        self.manager_role = Group.objects.create(name='Manager role')
        self.actor.groups.set([self.manager_role])
        self.target = Group.objects.create(name='target-role')
        self.root_role = Group.objects.create(name='Root role')
        self.root = get_user_model().objects.create_superuser(username='root', email='root@example.com')
        self.root.groups.set([self.root_role])
        self.warehouse = WareHouse.objects.create(name='UTT', address='Dhaka', phone='1', code='UTT')
        self.other = WareHouse.objects.create(name='Other', address='Dhaka', phone='2', code='OTHER')
        self.scope = RoleWarehouseAccess.objects.create(role=self.manager_role, all_warehouses=True,
            create_by='root', modified_by='root')
        self.assignment = RoleWarehouseAccess.objects.create(role=self.target, warehouse=self.warehouse,
            create_by='original', modified_by='original')
        self.client = APIClient(enforce_csrf_checks=True)
        self.login(self.actor)
        self.url = reverse('warehouse:role-warehouse-access-list')
        self.list_url = reverse('warehouse:role-warehouse-access-list-view')
        self.detail = reverse('warehouse:role-warehouse-access-detail', args=[self.assignment.pk])
        self.update = reverse('warehouse:role-warehouse-access-update', args=[self.assignment.pk])

    def login(self, user):
        self.client.force_login(user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def grant(self, *actions):
        names = ['manage_warehouse_access']
        self.manager_role.permissions.add(*Permission.objects.filter(content_type__app_label='warehouse', codename__in=names))

    def new_role(self, name='new-role'):
        return Group.objects.create(name=name)

    def test_management_permission_alone_grants_assignment_crud_and_audits(self):
        self.manager_role.permissions.set(Permission.objects.filter(content_type__app_label='warehouse',
            codename__in=['view_rolewarehouseaccess', 'add_rolewarehouseaccess', 'change_rolewarehouseaccess', 'delete_rolewarehouseaccess']))
        role = self.new_role()
        payload = {'role': role.pk, 'warehouse': self.warehouse.pk, 'all_warehouses': False}
        for method, url, data in [('get', self.url, {}), ('post', self.url, payload),
                                 ('patch', self.update, {}), ('delete', self.detail, {})]:
            self.assertEqual(getattr(self.client, method)(url, data, format='json').status_code, 403)
        self.manager_role.permissions.clear()
        self.grant()
        response = self.client.post(self.url, {**payload, 'create_by': 'spoof'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = response.data['role_warehouse_access']
        self.assertEqual(record['create_by'], self.actor.username)
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        response = self.client.patch(self.update, {'warehouse': None, 'all_warehouses': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['create_by'], 'original')
        self.assertEqual(response.data['modified_by'], self.actor.username)
        self.assertEqual(self.client.delete(self.detail).status_code, 204)
        self.assertTrue(Group.objects.filter(pk=self.target.pk).exists())

    def test_invalid_modes_duplicates_references_and_user_reassignment_are_rejected(self):
        self.grant('add', 'change')
        user = self.new_role()
        for payload in [
            {'role': user.pk, 'warehouse': self.warehouse.pk, 'all_warehouses': True},
            {'role': user.pk, 'warehouse': 999999}, {'role': 999999},
            {'role': self.target.pk, 'all_warehouses': True}, {},
        ]:
            self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 400)
        response = self.client.patch(self.update, {'all_warehouses': True}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.patch(self.update, {'role': user.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.role_id, self.target.pk)
        self.assertFalse(self.assignment.all_warehouses)

    def test_unassigned_state_can_be_saved_and_later_assigned(self):
        self.grant('add', 'change')
        user = self.new_role()
        response = self.client.post(self.url, {'role': user.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = response.data['role_warehouse_access']
        self.assertIsNone(record['warehouse'])
        self.assertFalse(record['all_warehouses'])
        response = self.client.patch(reverse('warehouse:role-warehouse-access-detail', args=[record['id']]),
                                     {'warehouse': self.warehouse.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_specific_warehouse_manager_cannot_grant_or_modify_outside_scope(self):
        self.grant('view', 'add', 'change', 'delete')
        self.scope.all_warehouses = False
        self.scope.warehouse = self.warehouse
        self.scope.save()
        foreign = RoleWarehouseAccess.objects.create(role=self.new_role('other-target'), warehouse=self.other)
        global_access = RoleWarehouseAccess.objects.create(role=self.new_role('global-target'), all_warehouses=True)
        unassigned = RoleWarehouseAccess.objects.create(role=self.new_role('unassigned-target'))
        rows = self.client.get(self.list_url).data['results']
        self.assertEqual({row['id'] for row in rows}, {self.scope.pk, self.assignment.pk, unassigned.pk})
        for assignment in [foreign, global_access]:
            url = reverse('warehouse:role-warehouse-access-detail', args=[assignment.pk])
            for method, data in [('get', {}), ('patch', {'warehouse': self.warehouse.pk}), ('delete', {})]:
                self.assertEqual(getattr(self.client, method)(url, data, format='json').status_code, 404)
        for changes in [{'warehouse': self.other.pk}, {'warehouse': None, 'all_warehouses': True}]:
            self.assertEqual(self.client.patch(self.update, changes, format='json').status_code, 403)
            payload = {'role': self.new_role('new-' + str(changes['warehouse'])).pk, **changes}
            self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 403)
        user = self.new_role('allowed-target')
        self.assertEqual(self.client.post(self.url, {'role': user.pk, 'warehouse': self.warehouse.pk}, format='json').status_code, 201)
        url = reverse('warehouse:role-warehouse-access-update', args=[unassigned.pk])
        self.assertEqual(self.client.patch(url, {'warehouse': self.warehouse.pk}, format='json').status_code, 200)

    def test_self_and_superuser_assignments_are_protected_from_non_superusers(self):
        self.grant('view', 'add', 'change', 'delete')
        own = reverse('warehouse:role-warehouse-access-detail', args=[self.scope.pk])
        for method, data in [('patch', {'all_warehouses': False}),
                             ('put', {'role': self.manager_role.pk, 'all_warehouses': False}), ('delete', {})]:
            self.assertEqual(getattr(self.client, method)(own, data, format='json').status_code, 403)
        response = self.client.post(self.url, {'role': self.root_role.pk, 'all_warehouses': True}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        root_assignment = RoleWarehouseAccess.objects.create(role=self.root_role, all_warehouses=True)
        url = reverse('warehouse:role-warehouse-access-detail', args=[root_assignment.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)

    def test_superuser_can_manage_self_and_any_scope_without_an_assignment(self):
        self.login(self.root)
        response = self.client.post(self.url, {'role': self.root_role.pk, 'all_warehouses': True}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        own = reverse('warehouse:role-warehouse-access-detail', args=[response.data['role_warehouse_access']['id']])
        self.assertEqual(self.client.patch(own, {'all_warehouses': False, 'warehouse': self.other.pk}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(own).status_code, 204)
        self.assertEqual(self.client.patch(self.update, {'warehouse': self.other.pk}, format='json').status_code, 200)

    def test_unassigned_manager_is_denied_despite_permissions(self):
        self.grant('view', 'add', 'change', 'delete')
        self.scope.all_warehouses = False
        self.scope.save()
        for remove in [False, True]:
            if remove:
                self.scope.delete()
            self.assertEqual(self.client.get(self.list_url).status_code, 403)
            self.assertEqual(self.client.patch(self.update, {}, format='json').status_code, 403)
            user = self.new_role('target-' + str(remove))
            self.assertEqual(self.client.post(self.url, {'role': user.pk, 'warehouse': self.warehouse.pk}, format='json').status_code, 403)

    def test_scoping_is_checked_again_when_an_assignment_changes_after_lookup(self):
        self.grant('change', 'delete')
        self.scope.all_warehouses = False
        self.scope.warehouse = self.warehouse
        self.scope.save()
        # The object/serializer may have been loaded before another manager moved it.
        request = type('Request', (), {'user': self.actor})()
        serializer = RoleWarehouseAccessSerializer(self.assignment, data={'warehouse': None}, partial=True,
                                                   context={'request': request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        RoleWarehouseAccess.objects.filter(pk=self.assignment.pk).update(warehouse=self.other)
        with self.assertRaises(PermissionDenied):
            serializer.save(modified_by=self.actor.username)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.warehouse_id, self.other.pk)

    def test_manager_scope_revocation_is_rechecked_before_save(self):
        self.grant('change')
        request = type('Request', (), {'user': self.actor})()
        serializer = RoleWarehouseAccessSerializer(self.assignment, data={'warehouse': self.other.pk}, partial=True,
                                                   context={'request': request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        RoleWarehouseAccess.objects.filter(pk=self.scope.pk).update(all_warehouses=False, warehouse=self.warehouse)
        with self.assertRaises(PermissionDenied):
            serializer.save(modified_by=self.actor.username)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.warehouse_id, self.warehouse.pk)

    def test_group_grants_revocation_session_and_csrf(self):
        self.assertEqual(APIClient().get(self.url).status_code, 403)
        group = Group.objects.create(name='Assignment managers')
        group.permissions.set(Permission.objects.filter(content_type__app_label='warehouse',
            codename__in=['manage_warehouse_access']))
        self.actor.groups.set([group])
        RoleWarehouseAccess.objects.create(role=group, all_warehouses=True)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.credentials()
        self.assertEqual(self.client.patch(self.update, {'warehouse': self.other.pk}, format='json').status_code, 403)
        self.login(self.actor)
        self.assertEqual(self.client.patch(self.update, {'warehouse': self.other.pk}, format='json').status_code, 200)
        group.permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_lists_search_filters_pagination_and_serialization_query_count(self):
        self.grant('view')
        self.assertEqual(self.client.get(self.list_url, {'q': 'target-role'}).data['count'], 1)
        self.assertEqual(self.client.get(self.list_url, {'q': 'UTT'}).data['count'], 1)
        self.assertEqual(self.client.get(self.list_url, {'role': self.target.pk}).data['count'], 1)
        self.assertEqual(self.client.get(self.list_url, {'warehouse': self.other.pk}).data['count'], 0)
        self.assertEqual(self.client.get(self.list_url, {'all_warehouses': 'true'}).data['count'], 1)
        for filters in [{'role': 0}, {'warehouse': 'bad'}, {'all_warehouses': 'bad'}]:
            self.assertEqual(self.client.get(self.list_url, filters).status_code, 400)
        for index in range(21):
            RoleWarehouseAccess.objects.create(role=self.new_role(f'page-{index}'), warehouse=self.warehouse)
        response = self.client.get(self.list_url)
        self.assertEqual(response.data['count'], 23)
        self.assertEqual(len(response.data['results']), 20)
        self.assertIsNotNone(response.data['next'])
        self.assertEqual(len(self.client.get(self.list_url, {'page': 2}).data['results']), 3)
        with self.assertNumQueries(1):
            rows = RoleWarehouseAccessSerializer(RoleWarehouseAccess.objects.select_related('role', 'warehouse'), many=True).data
        self.assertEqual(len(rows), 23)

    def test_methods_missing_records_form_parser_and_database_errors(self):
        self.grant('view', 'add', 'change', 'delete')
        for url in [self.url, self.list_url, self.detail]:
            self.assertEqual(self.client.head(url).status_code, 200)
            self.assertEqual(self.client.options(url).status_code, 200)
            self.assertIn('no-store', self.client.get(url)['Cache-Control'])
        self.assertEqual(self.client.post(self.list_url, {}, format='json').status_code, 405)
        self.assertEqual(self.client.get(self.update).status_code, 405)
        missing = reverse('warehouse:role-warehouse-access-detail', args=[999999])
        self.assertEqual(self.client.get(missing).status_code, 404)
        user = self.new_role()
        response = self.client.post(self.url, {'role': user.pk, 'warehouse': self.warehouse.pk}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        with patch('warehouse.serializers.RoleWarehouseAccessSerializer.save', side_effect=IntegrityError):
            self.assertEqual(self.client.patch(self.update, {'warehouse': self.other.pk}, format='json').status_code, 400)
        with patch('warehouse.serializers.RoleWarehouseAccessSerializer.save', side_effect=OperationalError('database is locked')):
            self.assertEqual(self.client.patch(self.update, {'warehouse': self.other.pk}, format='json').status_code, 409)

    def test_management_permission_allows_read_and_update(self):
        self.grant('change')
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        self.assertEqual(self.client.patch(self.update, {'warehouse': self.other.pk}, format='json').status_code, 200)
        self.assertEqual(self.client.options(self.update).status_code, 200)

    def test_old_user_assignment_endpoints_and_user_payloads_are_not_accepted(self):
        self.login(self.root)
        self.assertEqual(self.client.get('/warehouse/api/user-warehouse-access/').status_code, 404)
        role = self.new_role('Role payload validation')
        response = self.client.post(self.url, {'role': role.pk, 'user': self.actor.pk,
                                              'all_warehouses': True}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('user', response.data)
