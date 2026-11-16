from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from warehouse.models import RoleWarehouseAccess, WareHouse


class AssignmentManagementPermissionTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser(username='scope-root', email='scope@example.com')
        self.user = get_user_model().objects.create_user(username='warehouse-manager')
        self.role = Group.objects.create(name='Warehouse managers')
        self.user.groups.set([self.role])
        self.target = Group.objects.create(name='Warehouse readers')
        self.warehouse = WareHouse.objects.create(name='UTT', address='Dhaka', phone='1')
        RoleWarehouseAccess.objects.create(role=self.role, warehouse=self.warehouse)
        self.permission = Permission.objects.get(content_type__app_label='warehouse', codename='manage_warehouse_access')
        self.client = APIClient(enforce_csrf_checks=True)
        self.url = reverse('warehouse:role-warehouse-access-list')
        self.page = '/warehouse/role-warehouse-access/'

    def login(self, user):
        self.client.force_login(user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def test_checkbox_permission_is_listed_saved_in_role_and_inherited_then_revoked(self):
        self.login(self.root)
        permissions = self.client.get(reverse('account:permission-list')).data
        manage = next(row for row in permissions if row['id'] == self.permission.pk)
        self.assertEqual(manage['name'], 'Manage warehouse assignments')
        self.assertEqual(manage['model'], 'rolewarehouseaccess')
        url = reverse('account:group-permissions', args=[self.role.pk])
        self.assertEqual(self.client.put(url, {'permissions': [self.permission.pk]}, format='json').status_code, 200)
        self.login(self.user)
        self.assertNotContains(self.client.get(reverse('dashboard')), '>Warehouse assignments</a>')
        self.assertEqual(self.client.get(self.page).status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(reverse('account:group-list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('warehouse:stock-list')).status_code, 403)
        response = self.client.post(self.url, {'role': self.target.pk, 'warehouse': self.warehouse.pk,
                                              'all_warehouses': False}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        assignment = response.data['role_warehouse_access']
        self.assertEqual(self.client.delete(reverse('warehouse:role-warehouse-access-detail', args=[assignment['id']])).status_code, 204)
        self.login(self.root)
        self.assertEqual(self.client.put(url, {'permissions': []}, format='json').status_code, 200)
        self.login(self.user)
        self.assertEqual(self.client.get(self.page).status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), '>Warehouse assignments</a>')

    def test_standalone_page_is_removed_and_specific_role_options_remain_scoped(self):
        self.role.permissions.add(self.permission)
        other = WareHouse.objects.create(name='Other', address='Dhaka', phone='2')
        foreign = Group.objects.create(name='Foreign role')
        RoleWarehouseAccess.objects.create(role=foreign, warehouse=other)
        global_role = Group.objects.create(name='Global role')
        RoleWarehouseAccess.objects.create(role=global_role, all_warehouses=True)
        self.login(self.user)
        options = self.client.get(reverse('warehouse:role-warehouse-access-options'), {'role': self.target.pk})
        self.assertEqual(options.status_code, 200, options.data)
        self.assertEqual(options.data['role'], self.target.pk)
        for role in [self.role, foreign, global_role]:
            self.assertIn(self.client.get(reverse('warehouse:role-warehouse-access-options'), {'role': role.pk}).status_code, [403, 404])
        page = self.client.get(self.page, {'role': self.target.pk})
        self.assertEqual(page.status_code, 404)

    def test_direct_user_grants_do_not_enable_management(self):
        self.user.user_permissions.add(self.permission)
        self.login(self.user)
        self.assertEqual(self.client.get(self.page).status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 403)
