from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from account.permissions import ACCESS_RULES
from warehouse.models import RoleWarehouseAccess, WareHouse


class RoleWarehousePermissionPageTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser(username='warehouse-page-root', email='root@example.com')
        self.role = Group.objects.create(name='Warehouse readers')
        self.warehouse = WareHouse.objects.create(name='UTT', address='Dhaka', phone='1', code='UTT')
        self.other = WareHouse.objects.create(name='Other', address='Dhaka', phone='2')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.root)
        self.page = reverse('account_pages:role-permissions', args=[self.role.pk])
        self.options = reverse('warehouse:role-warehouse-access-options')

    def permission(self, name):
        app, code = name.split('.')
        return Permission.objects.get(content_type__app_label=app, codename=code)

    def test_permission_page_connects_scope_form_options_and_create_update_apis(self):
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role-warehouse-access')
        self.assertContains(response, f'data-role-id="{self.role.pk}"')
        self.assertContains(response, f'data-options-url="{self.options}?role={self.role.pk}"')
        self.assertContains(response, 'All warehouses')
        self.assertContains(response, 'Specific warehouse')
        self.assertContains(response, 'No warehouse access')
        self.assertContains(response, 'data-permissions-form')
        self.assertContains(response, 'id="role-permissions-form"', count=1)
        self.assertNotContains(response, 'data-warehouse-save')
        self.assertIn('no-store', response['Cache-Control'])

    def test_page_csrf_load_create_then_update_scope_roundtrip(self):
        page = self.client.get(self.page)
        token = self.client.cookies['csrftoken'].value
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        options = self.client.get(self.options, {'role': self.role.pk})
        self.assertEqual(options.status_code, 200, options.data)
        self.assertIsNone(options.data['assignment'])
        self.assertTrue(options.data['can_assign_all'])
        response = self.client.post(reverse('warehouse:role-warehouse-access-list'), {
            'role': self.role.pk, 'warehouse': self.warehouse.pk, 'all_warehouses': False,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = response.data['role_warehouse_access']
        self.assertEqual(self.client.get(self.options, {'role': self.role.pk}).data['assignment']['id'], record['id'])
        response = self.client.patch(reverse('warehouse:role-warehouse-access-update', args=[record['id']]),
            {'warehouse': None, 'all_warehouses': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['all_warehouses'])
        self.assertEqual(RoleWarehouseAccess.objects.filter(role=self.role).count(), 1)

    def test_page_with_only_crud_permission_management_keeps_original_editor(self):
        user = get_user_model().objects.create_user(username='crud-permission-manager')
        manager = Group.objects.create(name='CRUD permission manager')
        manager.permissions.set([self.permission(name) for name in ACCESS_RULES['manage_role_permissions']])
        user.groups.set([manager])
        self.client.force_login(user)
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-permissions-form')
        self.assertNotContains(response, 'data-role-warehouse-access')
        self.assertNotContains(response, 'data-warehouse-access-form')
        self.assertEqual(self.client.get(self.options, {'role': self.role.pk}).status_code, 403)

    def test_scoped_manager_options_need_no_warehouse_view_grant_and_cannot_offer_all(self):
        user = get_user_model().objects.create_user(username='utt-scope-manager')
        manager = Group.objects.create(name='UTT scope managers')
        manager.permissions.add(self.permission('warehouse.manage_warehouse_access'))
        user.groups.set([manager])
        RoleWarehouseAccess.objects.create(role=manager, warehouse=self.warehouse)
        self.client.force_login(user)
        response = self.client.get(self.options, {'role': self.role.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['value'] for row in response.data['warehouse']], [self.warehouse.pk])
        self.assertFalse(response.data['can_assign_all'])
        self.assertTrue(response.data['can_create'])
        self.assertTrue(response.data['can_change'])
        self.assertEqual(self.client.get(self.options, {'role': manager.pk}).status_code, 403)
        RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.other)
        self.assertEqual(self.client.get(self.options, {'role': self.role.pk}).status_code, 404)

    def test_options_validation_auth_methods_and_read_capabilities(self):
        self.assertEqual(APIClient().get(self.options, {'role': self.role.pk}).status_code, 403)
        self.assertEqual(self.client.get(self.options).status_code, 400)
        self.assertEqual(self.client.get(self.options, {'role': 'bad'}).status_code, 400)
        self.assertEqual(self.client.get(self.options, {'role': 999999}).status_code, 404)
        self.assertEqual(self.client.head(self.options, {'role': self.role.pk}).status_code, 200)
        self.assertEqual(self.client.options(self.options).status_code, 200)
        self.client.get(self.page)
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(self.client.post(self.options, {}).status_code, 405)
        response = self.client.get(self.options, {'role': self.role.pk})
        self.assertIn('no-store', response['Cache-Control'])
