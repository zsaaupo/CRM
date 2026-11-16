from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from account.permissions import ACCESS_RULES
from warehouse.models import RoleWarehouseAccess, WareHouse


class CombinedRoleSettingsTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser(username='settings-root', email='root@example.com')
        self.role = Group.objects.create(name='Combined settings role')
        self.warehouse = WareHouse.objects.create(name='UTT', address='Dhaka', phone='1')
        self.permission = Permission.objects.get(content_type__app_label='warehouse', codename='view_stock')
        self.client = APIClient(enforce_csrf_checks=True)
        self.login(self.root)
        self.url = reverse('account:group-permissions', args=[self.role.pk])

    def login(self, user):
        self.client.force_login(user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def test_single_request_creates_then_updates_both_settings(self):
        response = self.client.put(self.url, {'permissions': [self.permission.pk],
            'warehouse_access': {'warehouse': self.warehouse.pk, 'all_warehouses': False}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        record = response.data['warehouse_access']
        self.assertEqual(record['role'], self.role.pk)
        self.assertEqual(record['warehouse'], self.warehouse.pk)
        self.assertEqual(list(self.role.permissions.values_list('pk', flat=True)), [self.permission.pk])
        response = self.client.put(self.url, {'permissions': [],
            'warehouse_access': {'warehouse': None, 'all_warehouses': True}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['warehouse_access']['id'], record['id'])
        self.assertTrue(response.data['warehouse_access']['all_warehouses'])
        self.assertFalse(self.role.permissions.exists())
        self.assertEqual(RoleWarehouseAccess.objects.filter(role=self.role).count(), 1)

    def test_invalid_scope_does_not_save_permission_changes(self):
        self.role.permissions.add(self.permission)
        for settings in [
            {'warehouse': self.warehouse.pk, 'all_warehouses': True},
            {'warehouse': 999999, 'all_warehouses': False}, {},
            {'role': self.role.pk, 'warehouse': None, 'all_warehouses': True},
        ]:
            response = self.client.put(self.url, {'permissions': [], 'warehouse_access': settings}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertTrue(self.role.permissions.filter(pk=self.permission.pk).exists())
            self.assertFalse(RoleWarehouseAccess.objects.filter(role=self.role).exists())

    def test_late_failure_rolls_back_created_or_updated_scope_and_permissions(self):
        self.role.permissions.add(self.permission)
        for existing in [False, True]:
            if existing:
                RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.warehouse, create_by='original')
            with patch('account.api_views.GroupListSerializer', side_effect=IntegrityError):
                response = self.client.put(self.url, {'permissions': [],
                    'warehouse_access': {'warehouse': None, 'all_warehouses': True}}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertTrue(self.role.permissions.filter(pk=self.permission.pk).exists())
            if existing:
                assignment = RoleWarehouseAccess.objects.get(role=self.role)
                self.assertFalse(assignment.all_warehouses)
                self.assertEqual(assignment.warehouse_id, self.warehouse.pk)
                self.assertEqual(assignment.create_by, 'original')
            else:
                self.assertFalse(RoleWarehouseAccess.objects.filter(role=self.role).exists())

    def test_scope_requires_management_permission_and_permission_only_save_keeps_scope(self):
        user = get_user_model().objects.create_user(username='permission-only-manager')
        manager = Group.objects.create(name='Permission-only managers')
        for name in ACCESS_RULES['manage_role_permissions']:
            app, code = name.split('.')
            manager.permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
        user.groups.set([manager])
        RoleWarehouseAccess.objects.create(role=manager, all_warehouses=True)
        assignment = RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.warehouse)
        self.login(user)
        response = self.client.put(self.url, {'permissions': [self.permission.pk],
            'warehouse_access': {'warehouse': None, 'all_warehouses': True}}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(self.role.permissions.exists())
        response = self.client.put(self.url, {'permissions': [self.permission.pk]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        assignment.refresh_from_db()
        self.assertFalse(assignment.all_warehouses)
        self.assertEqual(assignment.warehouse_id, self.warehouse.pk)
