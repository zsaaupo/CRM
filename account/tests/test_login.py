from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from account.models import Account
from account.serializers import LoginSerializer


User = get_user_model()


class LoginSerializerTests(SimpleTestCase):
    def test_email_is_normalized_and_password_whitespace_is_preserved(self):
        serializer = LoginSerializer(data={
            'email': ' Person@Example.COM ',
            'password': '  unchanged password  ',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['email'], 'person@example.com')
        self.assertEqual(serializer.validated_data['password'], '  unchanged password  ')
        self.assertNotIn('password', serializer.data)

    def test_missing_blank_null_and_malformed_fields_are_rejected(self):
        cases = [
            ({}, 'email'),
            ({'email': 'person@example.com'}, 'password'),
            ({'email': '', 'password': 'test'}, 'email'),
            ({'email': None, 'password': 'test'}, 'email'),
            ({'email': 'invalid', 'password': 'test'}, 'email'),
            ({'email': 'person@example.com', 'password': ''}, 'password'),
            ({'email': 'person@example.com', 'password': None}, 'password'),
        ]
        for payload, field in cases:
            with self.subTest(field=field, payload=payload):
                serializer = LoginSerializer(data=payload)
                self.assertFalse(serializer.is_valid())
                self.assertIn(field, serializer.errors)

    def test_login_does_not_apply_registration_password_strength_rules(self):
        serializer = LoginSerializer(data={'email': 'person@example.com', 'password': '123'})
        self.assertTrue(serializer.is_valid(), serializer.errors)


class LoginAPIViewTests(APITestCase):
    def setUp(self):
        self.url = reverse('account:login')
        self.password = 'H7!rX2#tK9@vL4$p'
        self.user = User.objects.create_user(
            username='person@example.com', password=self.password, is_active=True,
        )
        self.account = Account.objects.create(
            user=self.user, full_name='Example Person', email='person@example.com',
            phone_number='',
        )
        self.client = APIClient(enforce_csrf_checks=True)
        bootstrap = self.client.get(self.url)
        self.token = bootstrap.data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=self.token)
        self.data = {'email': 'person@example.com', 'password': self.password}

    def test_get_issues_csrf_cookie_and_is_not_cacheable(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['csrfToken'])
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertIn('no-store', response['Cache-Control'])

    def test_verified_user_logs_in_and_session_is_available_on_later_requests(self):
        self.account.image = 'profile_pictures/avatar.png'
        self.account.save(update_fields=['image'])
        self.data['email'] = 'PERSON@EXAMPLE.COM'
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))
        self.assertEqual(response.data['user']['id'], self.user.pk)
        self.assertEqual(response.data['account']['id'], self.account.pk)
        self.assertEqual(response.data['account']['image'], self.account.image.url)
        self.assertEqual(response.data['user']['email'], 'person@example.com')
        self.assertTrue(response.cookies[settings.SESSION_COOKIE_NAME]['httponly'])
        self.assertNotIn(self.password, response.content.decode())
        self.assertNotIn('password', response.data['account'])
        self.assertNotIn('otp', response.data['account'])
        page = self.client.get(reverse('account_pages:signup'))
        self.assertTrue(page.wsgi_request.user.is_authenticated)
        self.assertEqual(page.wsgi_request.user.pk, self.user.pk)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)

    def test_wrong_password_unknown_email_and_inactive_user_share_failure(self):
        wrong = self.client.post(
            self.url, {**self.data, 'password': 'wrong'}, format='json',
        )
        unknown = self.client.post(
            self.url, {**self.data, 'email': 'unknown@example.com'}, format='json',
        )
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        inactive = self.client.post(self.url, self.data, format='json')
        for response in [wrong, unknown, inactive]:
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
            self.assertEqual(response.data, {'detail': 'Invalid email or password.'})
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_missing_csrf_cookie_and_token_reject_anonymous_login(self):
        anonymous = APIClient(enforce_csrf_checks=True)
        response = anonymous.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn(SESSION_KEY, anonymous.session)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.last_login)

    def test_invalid_csrf_token_is_rejected(self):
        self.client.credentials(HTTP_X_CSRFTOKEN='incorrect-token')
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_cross_origin_login_is_rejected_even_with_valid_token(self):
        response = self.client.post(
            self.url, self.data, format='json', HTTP_ORIGIN='https://untrusted.example',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_invalid_payload_returns_400_and_does_not_create_records(self):
        response = self.client.post(self.url, {'email': 'invalid'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)
        self.assertIn('password', response.data)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Account.objects.count(), 1)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_login_preserves_password_whitespace(self):
        password = '  H7!rX2#tK9@vL4$p  '
        self.user.set_password(password)
        self.user.save(update_fields=['password'])
        response = self.client.post(
            self.url, {**self.data, 'password': password}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_existing_password_is_accepted_without_new_strength_validation(self):
        self.user.set_password('123')
        self.user.save(update_fields=['password'])
        response = self.client.post(
            self.url, {**self.data, 'password': '123'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_rotates_anonymous_session_key_and_preserves_session_data(self):
        session = self.client.session
        session['test_marker'] = 'preserved'
        session.save()
        old_key = session.session_key
        self.client.cookies[settings.SESSION_COOKIE_NAME] = old_key
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(self.client.session.session_key, old_key)
        self.assertEqual(self.client.session['test_marker'], 'preserved')

    def test_login_rotates_csrf_and_returns_token_for_later_requests(self):
        old_cookie = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(self.client.cookies[settings.CSRF_COOKIE_NAME].value, old_cookie)
        rejected = self.client.post(self.url, self.data, format='json')
        self.assertEqual(rejected.status_code, status.HTTP_403_FORBIDDEN)
        self.client.credentials(HTTP_X_CSRFTOKEN=response.data['csrfToken'])
        accepted = self.client.post(self.url, self.data, format='json')
        self.assertEqual(accepted.status_code, status.HTTP_200_OK)

    def test_valid_user_without_account_can_authenticate_without_creating_profile(self):
        self.account.delete()
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['account'])
        self.assertFalse(Account.objects.exists())
