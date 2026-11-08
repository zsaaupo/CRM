from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core import mail
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .emails import _send_mail_in_background, send_mail, thread_send_email
from .models import Account
from .serializers import AccountSerializer, SignUpSerializer
from .utils import generate_otp, otp_matches


User = get_user_model()


class SignUpSerializerTests(TestCase):
    def signup_data(self, **overrides):
        data = {
            'full_name': 'Example Person',
            'email': 'person@example.com',
            'password': 'V7!qL2#rT9@xM4$p',
            'password_confirm': 'V7!qL2#rT9@xM4$p',
        }
        data.update(overrides)
        return data

    def test_valid_signup_normalizes_input_without_creating_records(self):
        serializer = SignUpSerializer(data=self.signup_data(
            email='Person@Example.COM', phone_number='+8801700000000',
        ))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['email'], 'person@example.com')
        self.assertEqual(serializer.validated_data['full_name'], 'Example Person')
        self.assertEqual(serializer.validated_data['phone_number'], '+8801700000000')
        self.assertFalse(User.objects.exists())
        self.assertFalse(Account.objects.exists())
        self.assertNotIn('password', serializer.data)
        self.assertNotIn('password_confirm', serializer.data)
        self.assertEqual(serializer.data['email'], 'person@example.com')

    def test_required_fields_reject_missing_blank_and_null_values(self):
        for field in ['full_name', 'email', 'password', 'password_confirm']:
            for value in ['missing', '', None]:
                with self.subTest(field=field, value=value):
                    data = self.signup_data()
                    if value == 'missing':
                        data.pop(field)
                    else:
                        data[field] = value
                    serializer = SignUpSerializer(data=data)
                    self.assertFalse(serializer.is_valid())
                    self.assertIn(field, serializer.errors)

    def test_password_confirmation_must_match(self):
        serializer = SignUpSerializer(data=self.signup_data(password_confirm='different'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('password_confirm', serializer.errors)

    def test_configured_password_validators_are_applied(self):
        for password in ['123', '123456789012', 'person@example.com']:
            with self.subTest(password=password):
                serializer = SignUpSerializer(data=self.signup_data(
                    password=password, password_confirm=password,
                ))
                self.assertFalse(serializer.is_valid())
                self.assertIn('password', serializer.errors)

    def test_password_whitespace_is_preserved(self):
        password = '  V7!qL2#rT9@xM4$p  '
        serializer = SignUpSerializer(data=self.signup_data(
            password=password, password_confirm=password,
        ))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['password'], password)
        self.assertEqual(serializer.validated_data['password_confirm'], password)

    def test_email_already_used_as_username_is_rejected(self):
        User.objects.create_user(username='PERSON@example.com')
        serializer = SignUpSerializer(data=self.signup_data())
        self.assertFalse(serializer.is_valid())
        self.assertIn('email', serializer.errors)

    def test_client_cannot_choose_owner_or_grant_staff_access(self):
        serializer = SignUpSerializer(data=self.signup_data(
            user=123, is_staff=True, is_superuser=True,
        ))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('user', serializer.validated_data)
        self.assertNotIn('is_staff', serializer.validated_data)
        self.assertNotIn('is_superuser', serializer.validated_data)

    def test_invalid_profile_fields_are_rejected(self):
        for field, value in [
            ('full_name', '   '),
            ('email', 'invalid'),
            ('email', 'a' * 140 + '@example.com'),
            ('phone_number', None),
            ('phone_number', '1' * 33),
        ]:
            with self.subTest(field=field):
                serializer = SignUpSerializer(data=self.signup_data(**{field: value}))
                self.assertFalse(serializer.is_valid())
                self.assertIn(field, serializer.errors)


class SignUpAPIViewTests(APITestCase):
    def setUp(self):
        email_patch = patch('account.views.thread_send_email')
        self.send_email = email_patch.start()
        self.addCleanup(email_patch.stop)
        self.url = reverse('account:signup')
        self.data = {
            'full_name': 'Example Person',
            'email': 'Person@Example.COM',
            'password': 'V7!qL2#rT9@xM4$p',
            'password_confirm': 'V7!qL2#rT9@xM4$p',
            'phone_number': '+8801700000000',
        }

    def test_anonymous_signup_creates_user_and_account(self):
        self.data['otp'] = '000000'
        with (
            patch('account.views.generate_otp', return_value='A0Z9B1'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(self.url, self.data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username='person@example.com')
        account = Account.objects.get(user=user)
        self.assertTrue(user.check_password(self.data['password']))
        self.assertNotEqual(user.password, self.data['password'])
        self.assertFalse(user.is_active)
        self.assertEqual(account.full_name, self.data['full_name'])
        self.assertEqual(account.email, user.username)
        self.assertEqual(account.phone_number, self.data['phone_number'])
        self.assertTrue(otp_matches('A0Z9B1', account.otp))
        self.assertFalse(otp_matches('000000', account.otp))
        self.assertGreater(account.otp_expires_at, timezone.now())
        self.send_email.assert_called_once()
        recipient, subject, body = self.send_email.call_args.args
        self.assertEqual(recipient, account.email)
        self.assertIn('A0Z9B1', body)
        self.assertIn('10 minutes', body)
        self.assertEqual(response.data['account']['id'], account.pk)
        self.assertNotIn('password', response.data['account'])
        self.assertNotIn('password_confirm', response.data['account'])
        self.assertNotIn('otp', response.data['account'])
        self.assertNotIn('otp_expires_at', response.data['account'])

    def test_email_thread_waits_until_commit_callback_runs(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.post(self.url, self.data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.send_email.assert_not_called()
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.send_email.assert_called_once()

    def test_phone_number_is_optional(self):
        self.data.pop('phone_number')
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Account.objects.get().phone_number, '')

    def test_missing_required_fields_return_400_without_creating_records(self):
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        for field in ['full_name', 'email', 'password', 'password_confirm']:
            self.assertIn(field, response.data)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Account.objects.exists())

    def test_invalid_input_returns_field_errors_without_creating_records(self):
        cases = [
            ({'full_name': '   '}, 'full_name'),
            ({'email': 'invalid'}, 'email'),
            ({'password_confirm': 'different'}, 'password_confirm'),
            ({'password': '123', 'password_confirm': '123'}, 'password'),
            ({'phone_number': None}, 'phone_number'),
            ({'phone_number': '1' * 33}, 'phone_number'),
        ]
        for overrides, error_field in cases:
            with self.subTest(overrides=overrides):
                response = self.client.post(
                    self.url, {**self.data, **overrides}, format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(error_field, response.data)
                self.assertFalse(User.objects.exists())
                self.assertFalse(Account.objects.exists())

    def test_duplicate_signup_returns_400_without_creating_another_account(self):
        self.client.post(self.url, self.data, format='json')
        self.data['email'] = 'PERSON@EXAMPLE.COM'
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Account.objects.count(), 1)

    def test_duplicate_created_after_validation_returns_400(self):
        User.objects.create_user(username='person@example.com')
        with patch('account.serializers.SignUpSerializer.email_exists', return_value=False):
            response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(Account.objects.exists())

    def test_account_failure_rolls_back_user_creation(self):
        with (
            patch('account.views.Account.objects.create', side_effect=IntegrityError),
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            with self.assertRaises(IntegrityError):
                self.client.post(self.url, self.data, format='json')
        self.assertFalse(User.objects.exists())
        self.assertFalse(Account.objects.exists())
        self.assertEqual(callbacks, [])
        self.send_email.assert_not_called()

    def test_client_cannot_choose_owner_or_grant_privileges(self):
        other_user = User.objects.create_user(username='other')
        self.data.update(user=other_user.pk, is_staff=True, is_superuser=True)
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        account = Account.objects.get()
        self.assertNotEqual(account.user_id, other_user.pk)
        self.assertFalse(account.user.is_staff)
        self.assertFalse(account.user.is_superuser)


class VerifyOTPAPIViewTests(APITestCase):
    def setUp(self):
        self.url = reverse('account:verify-otp')
        self.user = User.objects.create_user(
            username='person@example.com',
            password='V7!qL2#rT9@xM4$p',
            is_active=False,
        )
        self.account = Account.objects.create(
            user=self.user,
            full_name='Example Person',
            email='person@example.com',
            phone_number='+8801700000000',
            otp='A0Z9B1',
            otp_expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.data = {'email': 'person@example.com', 'otp': 'A0Z9B1'}

    def test_correct_code_activates_user_and_clears_verification_state(self):
        self.data['email'] = 'PERSON@EXAMPLE.COM'
        response = self.client.post(self.url, self.data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.account.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertIsNone(self.account.otp)
        self.assertIsNone(self.account.otp_expires_at)
        self.assertNotIn('otp', response.data)
        self.assertNotIn('password', response.data)

    def test_previous_hashed_codes_are_supported(self):
        self.account.otp = make_password(self.data['otp'])
        self.account.save(update_fields=['otp'])
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_missing_and_invalid_fields_return_400(self):
        cases = [
            ({}, 'email'),
            ({'email': 'person@example.com'}, 'otp'),
            ({'email': 'invalid', 'otp': 'A0Z9B1'}, 'email'),
            ({**self.data, 'otp': None}, 'otp'),
            ({**self.data, 'otp': ''}, 'otp'),
            ({**self.data, 'otp': 'A0Z9B'}, 'otp'),
            ({**self.data, 'otp': 'A0Z9B12'}, 'otp'),
            ({**self.data, 'otp': 'a0z9b1'}, 'otp'),
            ({**self.data, 'otp': 'A0Z9B!'}, 'otp'),
        ]
        for payload, error_field in cases:
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(error_field, response.data)
        self.user.refresh_from_db()
        self.account.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_wrong_code_does_not_activate_user_or_clear_code(self):
        response = self.client.post(
            self.url, {**self.data, 'otp': 'B1C2D3'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.account.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.account.otp, self.data['otp'])

    def test_unknown_email_and_wrong_code_return_same_error(self):
        unknown = self.client.post(
            self.url, {**self.data, 'email': 'unknown@example.com'}, format='json',
        )
        wrong = self.client.post(
            self.url, {**self.data, 'otp': 'B1C2D3'}, format='json',
        )
        self.assertEqual(unknown.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(unknown.data, wrong.data)

    def test_code_cannot_verify_a_different_account(self):
        other_user = User.objects.create_user(username='other@example.com', is_active=False)
        other_account = Account.objects.create(
            user=other_user, full_name='Other Person', email='other@example.com',
            phone_number='', otp='Z9Y8X7',
            otp_expires_at=timezone.now() + timedelta(minutes=10),
        )
        response = self.client.post(
            self.url, {**self.data, 'email': other_account.email}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        other_user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(other_user.is_active)

    def test_expired_code_does_not_activate_user(self):
        self.account.otp_expires_at = timezone.now() - timedelta(seconds=1)
        self.account.save(update_fields=['otp_expires_at'])
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_missing_code_or_expiry_is_rejected(self):
        for values in [{'otp': None}, {'otp': ''}, {'otp_expires_at': None}]:
            with self.subTest(values=values):
                Account.objects.filter(pk=self.account.pk).update(
                    otp='A0Z9B1',
                    otp_expires_at=timezone.now() + timedelta(minutes=10),
                )
                Account.objects.filter(pk=self.account.pk).update(**values)
                response = self.client.post(self.url, self.data, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.user.refresh_from_db()
                self.assertFalse(self.user.is_active)

    def test_successful_code_cannot_be_used_again(self):
        first = self.client.post(self.url, self.data, format='json')
        second = self.client.post(self.url, self.data, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_repeated_wrong_codes_do_not_invalidate_correct_code(self):
        for _ in range(6):
            response = self.client.post(
                self.url, {**self.data, 'otp': 'B1C2D3'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.account.refresh_from_db()
            self.assertEqual(self.account.otp, self.data['otp'])
            self.assertIsNotNone(self.account.otp_expires_at)

        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_active_user_is_not_reactivated_with_an_outstanding_code(self):
        self.user.is_active = True
        self.user.save(update_fields=['is_active'])
        response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_activation_failure_rolls_back_code_consumption(self):
        with patch('account.views.User.save', side_effect=RuntimeError('Save failed')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.data, format='json')
        self.account.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.account.otp, self.data['otp'])
        self.assertIsNotNone(self.account.otp_expires_at)
        self.assertFalse(self.user.is_active)


class ResendOTPAPIViewTests(APITestCase):
    def setUp(self):
        email_patch = patch('account.views.thread_send_email')
        self.send_email = email_patch.start()
        self.addCleanup(email_patch.stop)
        self.url = reverse('account:resend-otp')
        self.verify_url = reverse('account:verify-otp')
        self.user = User.objects.create_user(username='person@example.com', is_active=False)
        self.account = Account.objects.create(
            user=self.user,
            full_name='Example Person',
            email='person@example.com',
            phone_number='',
            otp='A0Z9B1',
            otp_expires_at=timezone.now() + timedelta(minutes=1),
        )
        self.data = {'email': 'person@example.com'}

    def test_resend_replaces_code_extends_expiry_and_emails_registered_address(self):
        requested_at = timezone.now()
        with (
            patch('account.views.generate_otp', return_value='Z9Y8X7'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                self.url, {'email': 'PERSON@EXAMPLE.COM'}, format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.account.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.account.otp, 'Z9Y8X7')
        self.assertGreaterEqual(
            self.account.otp_expires_at, requested_at + timedelta(minutes=10),
        )
        self.assertLessEqual(
            self.account.otp_expires_at, timezone.now() + timedelta(minutes=10),
        )
        self.assertFalse(self.user.is_active)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Account.objects.count(), 1)
        self.send_email.assert_called_once()
        recipient, subject, body = self.send_email.call_args.args
        self.assertEqual(recipient, self.account.email)
        self.assertIn('Z9Y8X7', body)
        self.assertIn('10 minutes', body)
        self.assertNotIn('A0Z9B1', body)
        self.assertEqual(set(response.data), {'message'})
        self.assertNotIn('Z9Y8X7', str(response.data))

    def test_email_waits_until_commit_callback_runs(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.send_email.assert_not_called()
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.send_email.assert_called_once()

    def test_missing_blank_null_or_malformed_email_is_rejected(self):
        for payload in [{}, {'email': ''}, {'email': None}, {'email': 'invalid'}]:
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload, format='json')
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('email', response.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.otp, 'A0Z9B1')
        self.send_email.assert_not_called()

    def test_unknown_email_does_not_send_or_create_records(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(
                self.url, {'email': 'unknown@example.com'}, format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Account.objects.count(), 1)
        self.assertEqual(callbacks, [])
        self.send_email.assert_not_called()

    def test_active_user_is_rejected_without_replacing_code(self):
        self.user.is_active = True
        self.user.save(update_fields=['is_active'])
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.account.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.account.otp, 'A0Z9B1')
        self.assertTrue(self.user.is_active)
        self.assertEqual(callbacks, [])
        self.send_email.assert_not_called()

    def test_user_without_account_is_rejected(self):
        User.objects.create_user(username='unlinked@example.com', is_active=False)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url, {'email': 'unlinked@example.com'}, format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Account.objects.count(), 1)
        self.send_email.assert_not_called()

    def test_expired_and_missing_codes_can_be_replaced(self):
        cases = [
            {'otp': 'A0Z9B1', 'otp_expires_at': timezone.now() - timedelta(minutes=1)},
            {'otp': None, 'otp_expires_at': None},
        ]
        for values in cases:
            with self.subTest(values=values):
                Account.objects.filter(pk=self.account.pk).update(**values)
                with patch('account.views.generate_otp', return_value='Z9Y8X7'):
                    response = self.client.post(self.url, self.data, format='json')
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.account.refresh_from_db()
                self.assertEqual(self.account.otp, 'Z9Y8X7')
                self.assertGreater(self.account.otp_expires_at, timezone.now())

    def test_old_code_is_rejected_and_new_code_can_verify(self):
        with patch('account.views.generate_otp', return_value='Z9Y8X7'):
            resend = self.client.post(self.url, self.data, format='json')
        old_code = self.client.post(
            self.verify_url, {**self.data, 'otp': 'A0Z9B1'}, format='json',
        )
        new_code = self.client.post(
            self.verify_url, {**self.data, 'otp': 'Z9Y8X7'}, format='json',
        )
        self.assertEqual(resend.status_code, status.HTTP_200_OK)
        self.assertEqual(old_code.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(new_code.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.account.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertIsNone(self.account.otp)

    def test_failed_update_preserves_old_code_and_does_not_email(self):
        original_expiry = self.account.otp_expires_at
        with (
            patch('account.views.Account.save', side_effect=RuntimeError('Save failed')),
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.data, format='json')
        self.account.refresh_from_db()
        self.assertEqual(self.account.otp, 'A0Z9B1')
        self.assertEqual(self.account.otp_expires_at, original_expiry)
        self.assertEqual(callbacks, [])
        self.send_email.assert_not_called()

    def test_generation_retries_if_it_matches_current_plain_or_hashed_code(self):
        for stored_code in ['A0Z9B1', make_password('A0Z9B1')]:
            with self.subTest(stored_format=stored_code[:6]):
                Account.objects.filter(pk=self.account.pk).update(otp=stored_code)
                with patch(
                    'account.views.generate_otp', side_effect=['A0Z9B1', 'Z9Y8X7'],
                ):
                    response = self.client.post(self.url, self.data, format='json')
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.account.refresh_from_db()
                self.assertEqual(self.account.otp, 'Z9Y8X7')

    def test_only_latest_code_works_after_multiple_resends(self):
        with patch('account.views.generate_otp', side_effect=['B1C2D3', 'Z9Y8X7']):
            self.client.post(self.url, self.data, format='json')
            self.client.post(self.url, self.data, format='json')
        previous = self.client.post(
            self.verify_url, {**self.data, 'otp': 'B1C2D3'}, format='json',
        )
        latest = self.client.post(
            self.verify_url, {**self.data, 'otp': 'Z9Y8X7'}, format='json',
        )
        self.assertEqual(previous.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(latest.status_code, status.HTTP_200_OK)


class OTPTests(SimpleTestCase):
    def test_codes_are_six_uppercase_alphanumeric_characters(self):
        for _ in range(100):
            otp = generate_otp()
            self.assertRegex(otp, r'\A[0-9A-Z]{6}\Z')
            self.assertTrue(any(char.isdigit() for char in otp))
            self.assertTrue(any(char.isalpha() for char in otp))

    def test_generator_rejects_letters_only_and_digits_only(self):
        with patch('account.utils.secrets.choice', side_effect='ABCDEF012345A0Z9B1'):
            self.assertEqual(generate_otp(), 'A0Z9B1')

    def test_account_serializer_does_not_accept_or_expose_otp(self):
        serializer = AccountSerializer(data={
            'otp': 'A0Z9B1',
            'otp_expires_at': '2099-01-01T00:00:00Z',
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('otp', serializer.validated_data)
        self.assertNotIn('otp_expires_at', serializer.validated_data)
        self.assertNotIn('otp', serializer.fields)


class EmailTests(SimpleTestCase):
    @override_settings(
        MAILERS={
            'default': {
                'BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
            },
        },
        DEFAULT_FROM_EMAIL='noreply@example.com',
    )
    def test_email_has_plain_text_and_escaped_complete_html(self):
        body = '<script>alert("test")</script>\r\nCode: A0Z9B1'
        sent = send_mail('recipient@example.com', 'Your OTP', body)
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['recipient@example.com'])
        self.assertEqual(message.from_email, 'noreply@example.com')
        self.assertEqual(message.body, body)
        html, mime_type = message.alternatives[0]
        self.assertEqual(mime_type, 'text/html')
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('<br>', html)
        self.assertTrue(html.endswith('</body></html>'))

    def test_thread_starts_with_email_arguments(self):
        with (
            patch('account.emails.Thread') as thread,
            patch('account.emails.send_mail', return_value=1) as send,
        ):
            thread_send_email('recipient@example.com', 'Your OTP', 'Code: A0Z9B1')
            thread.return_value.start.assert_called_once_with()
            options = thread.call_args.kwargs
            self.assertTrue(options['daemon'])
            options['target'](*options['args'])
            send.assert_called_once_with(
                'recipient@example.com', 'Your OTP', 'Code: A0Z9B1',
            )

    def test_background_email_failure_is_logged(self):
        with (
            patch('account.emails.send_mail', side_effect=RuntimeError('SMTP unavailable')),
            self.assertLogs('account.emails', level='ERROR') as captured,
        ):
            _send_mail_in_background('recipient@example.com', 'Your OTP', 'Code: A0Z9B1')
        self.assertIn('Could not send', captured.output[0])
        self.assertNotIn('A0Z9B1', captured.output[0])

    def test_failure_to_start_thread_is_logged(self):
        with (
            patch('account.emails.Thread') as thread,
            self.assertLogs('account.emails', level='ERROR') as captured,
        ):
            thread.return_value.start.side_effect = RuntimeError('No available thread')
            thread_send_email('recipient@example.com', 'Your OTP', 'Code: A0Z9B1')
        self.assertIn('Could not start', captured.output[0])

