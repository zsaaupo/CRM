from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from .models import Account
from .serializers import UserListSerializer


User = get_user_model()


class UserListAPIViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='manager@example.com', password='Manager!72Strong')
        self.role = Group.objects.create(name='User directory readers')
        self.role.permissions.add(Permission.objects.get(
            content_type__app_label='auth', codename='view_user',
        ))
        self.user.groups.add(self.role)
        self.account = Account.objects.create(
            user=self.user, full_name='Avery Morgan', email=self.user.username,
            phone_number='+8801700000000', designation='Teacher', otp='AB123C',
        )
        self.other = User.objects.create_user(username='pending@example.com', is_active=False)
        Account.objects.create(
            user=self.other, full_name='Pending Person', email=self.other.username,
            phone_number='', designation=None,
        )
        self.url = reverse('account:user-list')
        self.client.force_login(self.user)

    def test_user_with_group_permission_can_list_active_and_inactive_users(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertIsNone(response.data['next'])
        self.assertIsNone(response.data['previous'])
        self.assertEqual([row['id'] for row in response.data['results']], [self.other.pk, self.user.pk])
        self.assertFalse(response.data['results'][0]['is_active'])
        self.assertTrue(response.data['results'][1]['is_active'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_anonymous_and_inactive_sessions_cannot_list_users(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get(self.url).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_response_only_exposes_directory_fields(self):
        rows = self.client.get(self.url).data['results']
        expected = {'id', 'full_name', 'email', 'phone_number', 'designation', 'is_active', 'date_joined'}
        for row in rows:
            self.assertEqual(set(row), expected)
        self.assertEqual(rows[1]['phone_number'], '+8801700000000')
        self.assertEqual(rows[1]['designation'], 'Teacher')
        self.assertEqual(rows[1]['full_name'], 'Avery Morgan')

    def test_users_without_account_are_included_with_identity_fallbacks(self):
        user = User.objects.create_user(
            username='admin', email='admin@example.com', first_name='Site', last_name='Admin',
        )
        row = self.client.get(self.url).data['results'][0]
        self.assertEqual(row['id'], user.pk)
        self.assertEqual(row['full_name'], 'Site Admin')
        self.assertEqual(row['email'], 'admin@example.com')
        self.assertEqual(row['phone_number'], '')
        self.assertEqual(row['designation'], '')

    def test_case_insensitive_search_matches_name_email_phone_and_designation(self):
        for query in ['AVERY', 'MANAGER@EXAMPLE', '1700000000', 'TEACHER', '  Avery  ']:
            with self.subTest(query=query):
                response = self.client.get(self.url, {'q': query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['count'], 1)
                self.assertEqual(response.data['results'][0]['id'], self.user.pk)

    def test_no_matches_returns_empty_paginated_results(self):
        response = self.client.get(self.url, {'q': 'missing person'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])

    def test_pagination_is_bounded_and_has_stable_order(self):
        User.objects.bulk_create([User(username=f'person-{index}@example.com') for index in range(23)])
        first = self.client.get(self.url)
        second = self.client.get(first.data['next'])
        self.assertEqual(first.data['count'], 25)
        self.assertEqual(len(first.data['results']), 20)
        self.assertEqual(len(second.data['results']), 5)
        ids = [row['id'] for row in first.data['results'] + second.data['results']]
        self.assertEqual(len(set(ids)), 25)
        self.assertIsNone(second.data['next'])
        self.assertTrue(second.data['previous'])
        self.assertEqual(self.client.get(self.url, {'page': 99}).status_code, 404)

    def test_list_serialization_fetches_profiles_in_one_query(self):
        with self.assertNumQueries(1):
            rows = UserListSerializer(User.objects.select_related('account').order_by('pk'), many=True).data
        self.assertEqual(len(rows), 2)

    def test_post_does_not_create_users(self):
        response = self.client.post(self.url, {'email': 'extra@example.com'}, format='json')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(User.objects.count(), 2)


class UserManagementPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='creator@example.com', password='Creator!74#Secure')
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label='auth', codename='view_user',
        ))
        self.client = APIClient(enforce_csrf_checks=True)

    def test_user_management_pages_require_login(self):
        for name in ['account_pages:add-user', 'account_pages:user-list']:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(response, reverse('account_pages:login'), fetch_redirect_response=False)

    def test_add_user_page_has_csrf_fields_api_and_list_destination(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('account_pages:add-user'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/add_user.html')
        self.assertContains(response, f'action="{reverse("account:add-user")}"')
        self.assertContains(response, f'data-success-url="{reverse("account_pages:user-list")}"')
        self.assertContains(response, 'data-auth-form="add-user"')
        self.assertContains(response, 'name="password_confirm"')
        self.assertContains(response, 'name="designation"')
        self.assertContains(response, 'name="country"')
        self.assertContains(response, 'aria-controls="users-submenu" aria-expanded="true"')
        self.assertContains(response, 'src="/static/account/js/auth.js"')
        self.assertContains(response, 'src="/static/account/js/workspace.js"')
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertIn('no-store', response['Cache-Control'])

    def test_list_page_loads_api_and_does_not_embed_users_in_html(self):
        other = User.objects.create_user(username='not-in-html@example.com')
        self.client.force_login(self.user)
        response = self.client.get(reverse('account_pages:user-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/user_list.html')
        self.assertContains(response, f'data-api-url="{reverse("account:user-list")}"')
        self.assertContains(response, 'data-user-search')
        self.assertContains(response, f'data-assign-role-url="{reverse("account_pages:assign-role", args=[0])}"')
        self.assertContains(response, 'id="user-actions-template"')
        self.assertContains(response, 'title="Assign role"')
        self.assertContains(response, 'data-list-retry')
        self.assertContains(response, 'data-list-next')
        self.assertContains(response, 'src="/static/account/js/users.js"')
        self.assertNotContains(response, other.username)
        self.assertIn('no-store', response['Cache-Control'])

    def test_session_create_then_list_flow_with_real_csrf_checks(self):
        self.client.force_login(self.user)
        self.client.get(reverse('account_pages:add-user'))
        token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        created = self.client.post(reverse('account:add-user'), {
            'full_name': 'Added Through Page', 'email': 'newperson@example.com',
            'password': 'R8!xZ2#kT6@pM4$v', 'password_confirm': 'R8!xZ2#kT6@pM4$v',
        }, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(created.status_code, 201, created.data)
        page = self.client.get(reverse('account_pages:user-list'))
        self.assertEqual(page.status_code, 200)
        listing = self.client.get(reverse('account:user-list'), {'q': 'newperson@example.com'})
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data['count'], 1)
        self.assertEqual(listing.data['results'][0]['id'], created.data['account']['user'])
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))
