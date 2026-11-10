from .test_helpers import grant_permissions
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from unittest.mock import patch


User = get_user_model()


class RoleManagementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='manager@example.com')
        grant_permissions(self.user, 'auth.view_group', 'auth.add_group', 'auth.change_group', 'auth.delete_group', 'auth.view_permission', 'account.manage_access')
        self.group = Group.objects.create(name='Teachers')
        self.permission = Permission.objects.get(
            codename='view_account', content_type__app_label='account',
        )
        self.group.permissions.add(self.permission)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.url = reverse('account:group-detail', args=[self.group.pk])

    def test_anonymous_requests_cannot_read_edit_or_delete_roles(self):
        client = APIClient(enforce_csrf_checks=True)
        for method in ['get', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(client, method)(self.url)
                self.assertEqual(response.status_code, 403)
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())

    def test_role_detail_contains_current_permission_ids(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            'id': self.group.pk, 'name': 'Teachers', 'permissions': [self.permission.pk],
        })
        self.assertIn('no-store', response['Cache-Control'])

    def test_rename_preserves_memberships_and_permissions(self):
        self.user.groups.add(self.group)
        response = self.client.patch(self.url, {
            'name': '  Faculty  ', 'permissions': [], 'id': 99999,
        }, format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 200, response.data)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'Faculty')
        self.assertEqual(list(self.group.permissions.all()), [self.permission])
        self.assertTrue(self.user.groups.filter(pk=self.group.pk).exists())

    def test_invalid_names_are_rejected(self):
        Group.objects.create(name='Administrators')
        for name in ['', '   ', None, 'x' * 151, 'Administrators']:
            with self.subTest(name=name):
                response = self.client.patch(
                    self.url, {'name': name}, format='json', HTTP_X_CSRFTOKEN=self.token,
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn('name', response.data)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'Teachers')

    def test_concurrent_duplicate_name_returns_validation_error(self):
        Group.objects.create(name='Administrators')
        # Bypass the field validator to simulate another request winning the rename race.
        with patch('rest_framework.validators.UniqueValidator.__call__', return_value=None):
            response = self.client.patch(self.url, {'name': 'Administrators'}, format='json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'Teachers')

    def test_edits_and_deletion_require_csrf(self):
        self.assertEqual(self.client.patch(self.url, {'name': 'Faculty'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(self.url).status_code, 403)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'Teachers')

    def test_delete_removes_only_the_group_and_membership(self):
        self.user.groups.add(self.group)
        self.user.user_permissions.add(self.permission)
        response = self.client.delete(self.url, HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Group.objects.filter(pk=self.group.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(Permission.objects.filter(pk=self.permission.pk).exists())
        self.assertTrue(self.user.user_permissions.filter(pk=self.permission.pk).exists())
        self.assertFalse(self.user.groups.exists())

    def test_unknown_role_returns_404(self):
        url = reverse('account:group-detail', args=[99999])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_role_pages_require_login(self):
        client = APIClient()
        for name, args in [('account_pages:role-list', []), ('account_pages:add-role', []),
                           ('account_pages:role-permissions', [self.group.pk])]:
            response = client.get(reverse(name, args=args))
            self.assertRedirects(response, reverse('account_pages:login'), fetch_redirect_response=False)

    def test_role_list_template_contains_actions_icons_and_api_links(self):
        response = self.client.get(reverse('account_pages:role-list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-roles-toggle')
        self.assertContains(response, f'href="{reverse("account_pages:add-role")}"')
        self.assertContains(response, '>Add role</a>')
        self.assertContains(response, 'aria-controls="roles-submenu" aria-expanded="true"')
        self.assertContains(response, f'data-api-url="{reverse("account:group-list")}"')
        for title in ['Edit role', 'Manage permissions', 'Delete role']:
            self.assertContains(response, f'title="{title}"')
        self.assertContains(response, 'id="role-actions-template"')
        self.assertContains(response, 'data-edit-dialog')
        self.assertContains(response, 'data-delete-dialog')
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertIn('no-store', response['Cache-Control'])

    def test_add_role_page_can_create_role_and_return_to_directory(self):
        page = self.client.get(reverse('account_pages:add-role'))
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'account/add_role.html')
        self.assertContains(page, 'data-auth-form="add-role"')
        self.assertContains(page, f'action="{reverse("account:group-list")}"')
        self.assertContains(page, f'data-success-url="{reverse("account_pages:role-list")}"')
        self.assertContains(page, 'name="name"')
        self.assertContains(page, 'account/js/auth.js')
        self.assertIn('no-store', page['Cache-Control'])
        token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        response = self.client.post(
            reverse('account:group-list'), {'name': 'Sales manager'}, format='json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['permissions'], [])
        self.assertEqual(self.client.get(reverse('account_pages:role-list')).status_code, 200)
        roles = self.client.get(reverse('account:group-list')).data
        self.assertIn(response.data, roles)

    def test_permission_template_has_crud_columns_save_api_and_redirect_destination(self):
        self.group.name = '<script>unsafe</script>'
        self.group.save(update_fields=['name'])
        response = self.client.get(reverse('account_pages:role-permissions', args=[self.group.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-role-url="{self.url}"')
        self.assertContains(response, f'data-api-url="{reverse("account:permission-list")}"')
        for operation in ['Create', 'Read', 'Update', 'Delete']:
            self.assertContains(response, f'>{operation}</th>')
        self.assertContains(response, 'save to apply it to this role')
        self.assertContains(response, f'action="{reverse("account:group-permissions", args=[self.group.pk])}"')
        self.assertContains(response, f'data-success-url="{reverse("account_pages:role-list")}"')
        self.assertContains(response, 'form="role-permissions-form"')
        self.assertContains(response, 'data-permissions-save')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'data-permissions-reset')
        self.assertContains(response, '&lt;script&gt;unsafe&lt;/script&gt;')
        self.assertNotContains(response, '<script>unsafe</script>')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.group.permissions.count(), 1)

    def test_page_csrf_can_save_all_checked_permissions_then_load_role_list(self):
        page = self.client.get(reverse('account_pages:role-permissions', args=[self.group.pk]))
        self.assertEqual(page.status_code, 200)
        token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        custom = Permission.objects.create(
            content_type=self.permission.content_type, codename='export_account', name='Can export account',
        )
        response = self.client.put(
            reverse('account:group-permissions', args=[self.group.pk]),
            {'permissions': [self.permission.pk, custom.pk]}, format='json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertCountEqual(response.data['permissions'], [self.permission.pk, custom.pk])
        roles_page = self.client.get(reverse('account_pages:role-list'))
        self.assertEqual(roles_page.status_code, 200)
        roles = self.client.get(reverse('account:group-list'))
        role = next(role for role in roles.data if role['id'] == self.group.pk)
        self.assertCountEqual(role['permissions'], [self.permission.pk, custom.pk])

    def test_unknown_role_page_returns_404(self):
        response = self.client.get(reverse('account_pages:role-permissions', args=[99999]))
        self.assertEqual(response.status_code, 404)
