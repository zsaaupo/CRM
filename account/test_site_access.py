from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .permissions import ACCESS_RULES
from .test_helpers import grant_permissions


User = get_user_model()


class SiteAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='operator@example.com')
        self.target = User.objects.create_user(username='target@example.com')
        self.actor_role = Group.objects.create(name='Operator role')
        self.role = Group.objects.create(name='Target role')
        self.user.groups.add(self.actor_role)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)

    def grant(self, rule):
        permissions = []
        for name in ACCESS_RULES[rule]:
            app_label, codename = name.split('.', 1)
            permissions.append(Permission.objects.get(content_type__app_label=app_label, codename=codename))
        self.actor_role.permissions.set(permissions)

    def api_cases(self):
        return [
            ('get', 'account:user-list', [], {}, 'view_users', 200),
            ('post', 'account:add-user', [], {
                'full_name': 'New user', 'email': 'new@example.com',
                'password': 'R8!xZ2#kT6@pM4$v', 'password_confirm': 'R8!xZ2#kT6@pM4$v',
            }, 'add_user', 201),
            ('get', 'account:permission-list', [], {}, 'view_permissions', 200),
            ('get', 'account:group-list', [], {}, 'view_roles', 200),
            ('post', 'account:group-list', [], {'name': 'New role'}, 'add_role', 201),
            ('get', 'account:group-detail', [self.role.pk], {}, 'view_roles', 200),
            ('patch', 'account:group-detail', [self.role.pk], {'name': 'Renamed'}, 'change_role', 200),
            ('put', 'account:group-permissions', [self.role.pk], {'permissions': []}, 'manage_role_permissions', 200),
            ('get', 'account:user-groups', [self.target.pk], {}, 'view_users', 200),
            ('put', 'account:user-groups', [self.target.pk], {'group': self.role.pk}, 'assign_role', 200),
            ('post', 'account:assign-group', [], {'user': self.target.pk, 'group': self.role.pk}, 'assign_role', 200),
            ('delete', 'account:group-detail', [self.role.pk], {}, 'delete_role', 204),
        ]

    def test_every_management_api_denies_authenticated_users_without_permissions(self):
        for method, name, args, data, rule, expected in self.api_cases():
            with self.subTest(method=method, name=name):
                response = getattr(self.client, method)(reverse(name, args=args), data, format='json')
                self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(Group.objects.count(), 2)
        self.assertFalse(self.target.groups.exists())

    def test_each_api_accepts_its_permissions_inherited_from_a_group(self):
        for method, name, args, data, rule, expected in self.api_cases():
            with self.subTest(method=method, name=name):
                self.grant(rule)
                response = getattr(self.client, method)(reverse(name, args=args), data, format='json')
                self.assertEqual(response.status_code, expected, getattr(response, 'data', None))

    def test_all_management_pages_check_permissions(self):
        cases = [
            ('account_pages:user-list', [], 'view_users'),
            ('account_pages:add-user', [], 'add_user'),
            ('account_pages:role-list', [], 'view_roles'),
            ('account_pages:add-role', [], 'add_role'),
            ('account_pages:assign-role', [self.target.pk], 'assign_role'),
            ('account_pages:role-permissions', [self.role.pk], 'manage_role_permissions'),
        ]
        for name, args, rule in cases:
            with self.subTest(name=name):
                self.actor_role.permissions.clear()
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
                self.grant(rule)
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_model_permissions_alone_cannot_grant_privileged_access(self):
        grant_permissions(self.user, 'auth.change_user', 'auth.view_user', 'auth.view_group',
                          'auth.change_group', 'auth.view_permission', 'auth.delete_group')
        for method, name, args, data in [
            ('post', 'account:assign-group', [], {'user': self.target.pk, 'group': self.role.pk}),
            ('put', 'account:user-groups', [self.target.pk], {'group': self.role.pk}),
            ('put', 'account:group-permissions', [self.role.pk], {'permissions': []}),
            ('delete', 'account:group-detail', [self.role.pk], {}),
        ]:
            response = getattr(self.client, method)(reverse(name, args=args), data, format='json')
            self.assertEqual(response.status_code, 403)
        self.assertFalse(self.target.groups.exists())

    def test_delegated_administrator_cannot_change_self_or_superuser(self):
        self.grant('assign_role')
        root = User.objects.create_user(username='root@example.com', is_superuser=True, is_staff=True)
        for target in [self.user, root]:
            self.assertEqual(self.client.post(reverse('account:assign-group'), {
                'user': target.pk, 'group': self.role.pk,
            }, format='json').status_code, 403)
            self.assertEqual(self.client.put(reverse('account:user-groups', args=[target.pk]), {
                'group': self.role.pk,
            }, format='json').status_code, 403)
        self.grant('manage_role_permissions')
        self.assertEqual(self.client.put(reverse('account:group-permissions', args=[self.actor_role.pk]),
                                         {'permissions': []}, format='json').status_code, 403)
        self.assertTrue(self.actor_role.permissions.exists())
        self.assertEqual(self.client.get(reverse('account_pages:role-permissions',
                                                args=[self.actor_role.pk])).status_code, 403)

    def test_read_only_pages_hide_write_actions_and_inaccessible_navigation(self):
        self.grant('view_users')
        page = self.client.get(reverse('account_pages:user-list'))
        self.assertNotContains(page, 'title="Assign role"')
        self.assertNotContains(page, f'href="{reverse("account_pages:add-user")}"')
        self.assertNotContains(page, 'data-roles-toggle')
        self.grant('view_roles')
        page = self.client.get(reverse('account_pages:role-list'))
        for marker in ['data-role-edit title', 'data-role-manage title', 'data-role-delete title']:
            self.assertNotContains(page, marker)
        self.assertNotContains(page, f'href="{reverse("account_pages:add-role")}"')

    def test_create_only_users_return_to_dashboard_instead_of_forbidden_lists(self):
        for page_name, rule in [('account_pages:add-user', 'add_user'), ('account_pages:add-role', 'add_role')]:
            self.grant(rule)
            page = self.client.get(reverse(page_name))
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, f'data-success-url="{reverse("dashboard")}"')

    def test_unprivileged_users_keep_personal_pages_and_logout(self):
        for name in ['dashboard', 'account_pages:profile']:
            page = self.client.get(reverse(name))
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, 'data-users-toggle')
            self.assertNotContains(page, 'data-roles-toggle')
        self.assertEqual(self.client.post(reverse('account:logout')).status_code, 200)

    def test_staff_cannot_bypass_rules_through_django_admin(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        grant_permissions(self.user, 'auth.add_user', 'auth.change_user', 'auth.delete_user',
                          'auth.add_group', 'auth.change_group', 'auth.delete_group')
        request = RequestFactory().get('/admin/')
        request.user = User.objects.get(pk=self.user.pk)
        for model in [User, Group]:
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
        request.user.is_superuser = True
        for model in [User, Group]:
            self.assertTrue(admin.site._registry[model].has_change_permission(request))
