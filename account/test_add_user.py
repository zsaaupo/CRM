from .test_helpers import grant_permissions
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY, get_user_model
from django.db import IntegrityError
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from .models import Account


User = get_user_model()


class AddUserAPIViewTests(APITestCase):
    def setUp(self):
        self.url = reverse('account:add-user')
        self.creator = User.objects.create_user(
            username='creator@example.com', password='Creator!74#Secure',
        )
        grant_permissions(self.creator, 'auth.add_user')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.creator)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        self.data = {
            'full_name': 'New Person',
            'email': 'New.Person@Example.COM',
            'password': 'R8!xZ2#kT6@pM4$v',
            'password_confirm': 'R8!xZ2#kT6@pM4$v',
            'phone_number': '+8801700000000',
            'designation': 'Teacher',
        }

    def test_regular_authenticated_user_can_create_active_user_and_account(self):
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(username='new.person@example.com')
        account = user.account
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password(self.data['password']))
        self.assertNotEqual(user.password, self.data['password'])
        self.assertEqual(user.email, 'new.person@example.com')
        self.assertEqual(account.email, user.email)
        self.assertEqual(account.full_name, self.data['full_name'])
        self.assertEqual(account.designation, 'Teacher')
        self.assertEqual(account.phone_number, self.data['phone_number'])
        self.assertIsNone(account.otp)
        self.assertIsNone(account.otp_expires_at)
        self.assertEqual(response.data['account']['user'], user.pk)
        self.assertNotIn('password', response.data['account'])
        self.assertNotIn('password_confirm', response.data['account'])
        self.assertNotIn('otp', response.data['account'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.session[SESSION_KEY], str(self.creator.pk))
        login_client = APIClient()
        login = login_client.post(reverse('account:login'), {
            'email': user.email, 'password': self.data['password'],
        }, format='json')
        self.assertEqual(login.status_code, 200)

    def test_optional_profile_fields_are_saved(self):
        details = {
            'date_of_birth': '1995-06-15', 'gender': 'F', 'address': '12 Main Street',
            'city': 'Dhaka', 'state': 'Dhaka', 'postal_code': '1200', 'country': 'Bangladesh',
        }
        response = self.client.post(self.url, {**self.data, **details}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        for field, value in details.items():
            self.assertEqual(response.data['account'][field], value)

    def test_optional_fields_can_be_omitted(self):
        data = {k: v for k, v in self.data.items() if k not in ['phone_number', 'designation']}
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['account']['phone_number'], '')

    def test_anonymous_and_inactive_users_cannot_create_users(self):
        anonymous = APIClient(enforce_csrf_checks=True)
        self.assertEqual(anonymous.post(self.url, self.data, format='json').status_code, 403)
        self.creator.is_active = False
        self.creator.save(update_fields=['is_active'])
        self.assertEqual(self.client.post(self.url, self.data, format='json').status_code, 403)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(Account.objects.exists())

    def test_session_request_requires_csrf(self):
        self.client.credentials()
        self.assertEqual(self.client.post(self.url, self.data, format='json').status_code, 403)
        self.assertEqual(User.objects.count(), 1)

    def test_invalid_input_creates_no_records(self):
        cases = [
            ({'full_name': '   '}, 'full_name'),
            ({'email': 'invalid'}, 'email'),
            ({'password_confirm': 'different'}, 'password_confirm'),
            ({'password': '123', 'password_confirm': '123'}, 'password'),
            ({'phone_number': '1' * 33}, 'phone_number'),
            ({'date_of_birth': 'invalid'}, 'date_of_birth'),
            ({'gender': 'invalid'}, 'gender'),
        ]
        for changes, field in cases:
            with self.subTest(field=field):
                response = self.client.post(self.url, {**self.data, **changes}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(Account.objects.exists())

    def test_duplicate_email_is_rejected_case_insensitively(self):
        response = self.client.post(self.url, {
            **self.data, 'email': 'CREATOR@EXAMPLE.COM',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.data)
        self.assertEqual(User.objects.count(), 1)

    def test_client_cannot_set_privileges_or_link_another_user(self):
        response = self.client.post(self.url, {
            **self.data, 'is_staff': True, 'is_superuser': True, 'is_active': False,
            'user': self.creator.pk, 'groups': [1], 'user_permissions': [1], 'otp': 'ABC123',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(username='new.person@example.com')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertFalse(user.groups.exists())
        self.assertFalse(user.user_permissions.exists())
        self.assertNotEqual(user.pk, self.creator.pk)
        self.assertIsNone(user.account.otp)

    def test_account_save_failure_rolls_back_user_creation(self):
        with patch('account.api_views.Account.objects.create', side_effect=IntegrityError('failure')):
            with self.assertRaises(IntegrityError):
                self.client.post(self.url, self.data, format='json')
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(Account.objects.exists())

    def test_concurrent_duplicate_is_reported_as_validation_error(self):
        User.objects.create_user(username='new.person@example.com', password='Existing!42Pass')
        with patch('account.serializers.SignUpSerializer.email_exists', return_value=False):
            response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.data)
        self.assertEqual(User.objects.count(), 2)
        self.assertFalse(Account.objects.exists())

    def test_get_does_not_create_users(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(User.objects.count(), 1)
