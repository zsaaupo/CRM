from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient


User = get_user_model()


class UserListAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='reader@example.com')
        self.role = Group.objects.create(name='Directory readers')
        self.permission = Permission.objects.get(
            content_type__app_label='auth', codename='view_user',
        )
        self.api_url = reverse('account:user-list')
        self.page_url = reverse('account_pages:user-list')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def assert_access(self, status_code):
        for url in [self.api_url, self.page_url]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status_code)
                if status_code == 200:
                    self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.head(self.api_url).status_code, status_code)

    def test_authenticated_user_without_permission_is_denied_and_link_hidden(self):
        self.assert_access(403)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{self.page_url}"')

    def test_empty_group_wrong_permission_and_staff_flag_do_not_grant_access(self):
        self.user.groups.add(self.role)
        self.role.permissions.add(Permission.objects.get(
            content_type__app_label='account', codename='view_account',
        ))
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assert_access(403)

    def test_group_grant_allows_both_endpoints_and_navigation(self):
        self.role.permissions.add(self.permission)
        self.user.groups.add(self.role)
        self.assert_access(200)
        self.assertContains(self.client.get(reverse('dashboard')), f'href="{self.page_url}"')

    def test_revoking_group_permission_takes_effect_without_logging_out(self):
        self.role.permissions.add(self.permission)
        self.user.groups.add(self.role)
        self.assert_access(200)
        self.role.permissions.remove(self.permission)
        self.assert_access(403)
        self.assertNotContains(self.client.get(reverse('dashboard')), f'href="{self.page_url}"')

    def test_changing_assigned_group_removes_previous_access(self):
        self.role.permissions.add(self.permission)
        self.user.groups.add(self.role)
        self.assert_access(200)
        self.user.groups.set([Group.objects.create(name='No directory access')])
        self.assert_access(403)

    def test_direct_permission_and_active_superuser_are_supported(self):
        self.user.user_permissions.add(self.permission)
        self.assert_access(200)
        self.user.user_permissions.clear()
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.assert_access(200)

    def test_anonymous_and_inactive_users_cannot_use_granted_permissions(self):
        self.role.permissions.add(self.permission)
        self.user.groups.add(self.role)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        for client in [self.client, APIClient()]:
            self.assertEqual(client.get(self.api_url).status_code, 403)
            self.assertRedirects(
                client.get(self.page_url), reverse('account_pages:login'),
                fetch_redirect_response=False,
            )
