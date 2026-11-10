from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from .models import Account


User = get_user_model()


class WorkspacePageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='avery@example.com', password='H7!rX2#tK9@vL4$p', is_active=True,
        )
        self.account = Account.objects.create(
            user=self.user, full_name='Avery Morgan', email='avery@example.com',
            phone_number='+8801700000000', designation='Manager', country='Bangladesh',
        )
        self.client = APIClient(enforce_csrf_checks=True)

    def test_dashboard_and_profile_require_login(self):
        for name in ['dashboard', 'account_pages:profile']:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(
                    response, reverse('account_pages:login'), fetch_redirect_response=False,
                )

    def test_login_page_has_csrf_api_target_and_dashboard_destination(self):
        response = self.client.get(reverse('account_pages:login'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTemplateUsed(response, 'account/login.html')
        self.assertContains(response, 'data-auth-form="login"')
        self.assertContains(response, f'action="{reverse("account:login")}"')
        self.assertContains(response, f'data-success-url="{reverse("dashboard")}"')
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)

    def test_authenticated_user_visiting_login_goes_to_dashboard(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('account_pages:login'))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_dashboard_shows_current_user_and_workspace_navigation(self):
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label='auth', codename='view_user',
        ))
        other = User.objects.create_user(username='other@example.com')
        Account.objects.create(user=other, full_name='Other Private Person', email='other@example.com', phone_number='')
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTemplateUsed(response, 'account/dashboard.html')
        self.assertContains(response, 'Avery Morgan')
        self.assertContains(response, 'avery@example.com')
        self.assertNotContains(response, 'Other Private Person')
        self.assertNotContains(response, 'other@example.com')
        self.assertContains(response, 'data-nav-item', count=4)
        self.assertContains(response, 'data-users-toggle')
        self.assertContains(response, f'href="{reverse("account_pages:add-user")}"')
        self.assertContains(response, f'href="{reverse("account_pages:user-list")}"')
        self.assertContains(response, f'href="{reverse("account_pages:profile")}"')
        self.assertContains(response, f'action="{reverse("account:logout")}"')
        self.assertContains(response, 'data-user-menu-toggle')
        self.assertIn('no-store', response['Cache-Control'])

    def test_profile_shows_current_account_details(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('account_pages:profile'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, 'Avery Morgan')
        self.assertContains(response, 'Manager')
        self.assertContains(response, 'Bangladesh')
        self.assertNotContains(response, 'otp_expires_at')

    def test_missing_account_uses_user_identity_without_crashing(self):
        self.account.delete()
        self.client.force_login(self.user)
        for name in ['dashboard', 'account_pages:profile']:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertContains(response, 'avery@example.com')

    def test_profile_text_is_html_escaped(self):
        self.account.full_name = '<script>alert("profile")</script>'
        self.account.save(update_fields=['full_name'])
        self.client.force_login(self.user)
        response = self.client.get(reverse('account_pages:profile'))
        self.assertNotContains(response, '<script>alert("profile")</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_login_ignores_external_redirect_input(self):
        response = self.client.get(reverse('account_pages:login'), {'next': 'https://untrusted.example'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, f'data-success-url="{reverse("dashboard")}"')
        self.assertNotContains(response, 'https://untrusted.example')

    def test_login_dashboard_logout_flow_invalidates_session(self):
        self.client.get(reverse('account_pages:login'))
        token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        login = self.client.post(
            reverse('account:login'),
            {'email': 'avery@example.com', 'password': 'H7!rX2#tK9@vL4$p'},
            format='json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        old_session_key = self.client.session.session_key
        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.status_code, status.HTTP_200_OK)
        logout = self.client.post(
            reverse('account:logout'), {}, format='json',
            HTTP_X_CSRFTOKEN=login.data['csrfToken'],
        )
        self.assertEqual(logout.status_code, status.HTTP_200_OK)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertFalse(Session.objects.filter(session_key=old_session_key).exists())
        denied = self.client.get(reverse('dashboard'))
        self.assertRedirects(denied, reverse('account_pages:login'), fetch_redirect_response=False)
        stale_client = APIClient()
        stale_client.cookies[settings.SESSION_COOKIE_NAME] = old_session_key
        stale = stale_client.get(reverse('dashboard'))
        self.assertRedirects(stale, reverse('account_pages:login'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_get_logout_does_not_end_session(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('account:logout'))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertIn(SESSION_KEY, self.client.session)

    def test_logout_requires_csrf_even_when_logged_in(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('account:logout'), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn(SESSION_KEY, self.client.session)

    def test_anonymous_user_cannot_call_logout(self):
        token_response = self.client.get(reverse('account:login'))
        response = self.client.post(
            reverse('account:logout'), {}, format='json',
            HTTP_X_CSRFTOKEN=token_response.data['csrfToken'],
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_home_leads_to_dashboard_then_login_for_anonymous_user(self):
        response = self.client.get(reverse('home'), follow=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTemplateUsed(response, 'account/login.html')
