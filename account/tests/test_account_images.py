from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient, APITestCase

from account.models import Account
from .test_helpers import grant_permissions


class AccountImageAPITests(APITestCase):
    def setUp(self):
        media_root = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=media_root))
        self.enterContext(patch('account.api_views.thread_send_email'))
        self.client = APIClient(enforce_csrf_checks=True)
        self.data = {
            'full_name': 'Profile Picture User',
            'email': 'picture@example.com',
            'password': 'R8!xZ2#kT6@pM4$v',
            'password_confirm': 'R8!xZ2#kT6@pM4$v',
        }

    def test_signup_ignores_image_and_keeps_it_empty_through_login_and_directory(self):
        content = BytesIO()
        Image.new('RGB', (2, 2)).save(content, format='PNG')
        upload = SimpleUploadedFile('picture.png', content.getvalue(), content_type='image/png')
        with patch('account.api_views.generate_otp', return_value='A1B2C3'):
            signup = self.client.post(reverse('account:signup'),
                                      {**self.data, 'image': upload}, format='multipart')
        self.assertEqual(signup.status_code, 201, signup.data)
        account = Account.objects.get(email=self.data['email'])
        self.assertIsNone(signup.data['account']['image'])
        self.assertFalse(account.image)
        self.assertFalse(account.image.storage.exists('profile_pictures/picture.png'))

        verified = self.client.post(reverse('account:verify-otp'), {
            'email': self.data['email'], 'otp': 'A1B2C3',
        }, format='json')
        self.assertEqual(verified.status_code, 200, verified.data)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        login = self.client.post(reverse('account:login'), {
            'email': self.data['email'], 'password': self.data['password'],
        }, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(login.status_code, 200, login.data)
        self.assertEqual(login.data['account']['image'], signup.data['account']['image'])

        grant_permissions(account.user, 'auth.view_user')
        listing = self.client.get(reverse('account:user-list'))
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual(listing.data['results'][0]['image'], signup.data['account']['image'])
        account.refresh_from_db()
        self.assertIsNone(account.otp)
        self.assertFalse(account.image)

    def test_json_signup_without_image_returns_null(self):
        signup = self.client.post(reverse('account:signup'), self.data, format='json')
        self.assertEqual(signup.status_code, 201, signup.data)
        self.assertIsNone(signup.data['account']['image'])
        self.assertFalse(Account.objects.get().image)

    def test_signup_ignores_unsupported_image_instead_of_validating_it(self):
        upload = SimpleUploadedFile('picture.png', b'not an image', content_type='image/png')
        response = self.client.post(reverse('account:signup'),
                                    {**self.data, 'image': upload}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['account']['image'])
        self.assertFalse(Account.objects.get().image)
