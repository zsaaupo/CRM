from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from account.models import Account
from .test_helpers import grant_permissions


User = get_user_model()


class EditUserTests(TestCase):
    def setUp(self):
        self.enterContext(override_settings(MEDIA_ROOT=self.enterContext(TemporaryDirectory())))
        self.actor = User.objects.create_user(username='editor@example.com')
        grant_permissions(self.actor, 'auth.change_user', 'auth.view_user')
        editor_role = Group.objects.create(name='User editors')
        editor_role.permissions.set(self.actor.user_permissions.all())
        self.actor.user_permissions.clear()
        self.actor.groups.set([editor_role])
        self.target = User.objects.create_user(username='target@example.com', email='target@example.com',
            password='Original!74#Secure')
        self.account = Account.objects.create(user=self.target, full_name='Target User',
            email=self.target.email, phone_number='123', city='Dhaka', create_by='original')
        self.role = Group.objects.create(name='Original role')
        self.target.groups.set([self.role])
        self.url = reverse('account:edit-user', args=[self.target.pk])
        self.page = reverse('account_pages:edit-user', args=[self.target.pk])
        self.client = APIClient(enforce_csrf_checks=True)
        self.login(self.actor)

    def login(self, user):
        self.client.force_login(user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        self.client.credentials(HTTP_X_CSRFTOKEN=token)

    def test_group_permission_allows_loading_and_editing_profile(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['user'], self.target.pk)
        self.assertEqual(response.data['city'], 'Dhaka')
        for secret in ['password', 'otp', 'otp_expires_at']:
            self.assertNotIn(secret, response.data)
        self.assertIn('no-store', response['Cache-Control'])
        response = self.client.patch(self.url, {'full_name': 'Updated User', 'designation': 'Manager',
            'date_of_birth': '1990-02-20', 'gender': 'F', 'phone_number': '', 'country': 'Bangladesh'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.full_name, 'Updated User')
        self.assertEqual(self.account.city, 'Dhaka')
        self.assertEqual(self.account.modified_by, self.actor.username)
        self.assertEqual(self.account.create_by, 'original')
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password('Original!74#Secure'))
        self.assertEqual(list(self.target.groups.all()), [self.role])

    def test_email_change_updates_login_and_preserves_actor_session(self):
        response = self.client.patch(self.url, {'email': 'Updated@Example.COM'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.target.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual(self.target.username, 'updated@example.com')
        self.assertEqual(self.target.email, self.account.email)
        result = APIClient().post(reverse('account:login'), {
            'email': 'updated@example.com', 'password': 'Original!74#Secure'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.actor.pk))

    def test_same_email_is_valid_and_duplicates_are_case_insensitive(self):
        response = self.client.patch(self.url, {'email': 'TARGET@example.com'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        User.objects.create_user(username='taken@example.com')
        for value in ['TAKEN@example.com', 'not-email']:
            response = self.client.patch(self.url, {'email': value, 'full_name': 'Should not save'}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.account.refresh_from_db()
            self.assertEqual(self.account.full_name, 'Target User')

    def test_unauthorized_and_missing_csrf_requests_cannot_write(self):
        anonymous = APIClient(enforce_csrf_checks=True)
        self.assertEqual(anonymous.get(self.url).status_code, 403)
        self.assertEqual(anonymous.patch(self.url, {'full_name': 'No'}, format='json').status_code, 403)
        no_csrf = APIClient(enforce_csrf_checks=True); no_csrf.force_login(self.actor)
        self.assertEqual(no_csrf.patch(self.url, {'full_name': 'No'}, format='json').status_code, 403)
        self.actor.user_permissions.clear(); self.actor.groups.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.patch(self.url, {'full_name': 'No'}, format='json').status_code, 403)
        self.assertEqual(self.client.get(self.page).status_code, 403)

    def test_superuser_edit_requires_superuser_and_self_edit_is_allowed(self):
        self.target.is_superuser = True; self.target.save(update_fields=['is_superuser'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.assertEqual(self.client.patch(self.url, {'email': 'stolen@example.com'}, format='json').status_code, 403)
        self.actor.is_superuser = True; self.actor.save(update_fields=['is_superuser'])
        self.assertEqual(self.client.patch(self.url, {'city': 'Chattogram'}, format='json').status_code, 200)
        self.actor.is_superuser = False; self.actor.save(update_fields=['is_superuser'])
        response = self.client.patch(reverse('account:edit-user', args=[self.actor.pk]),
            {'full_name': 'Own Name'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_privileged_fields_cannot_be_written(self):
        for field, value in [('groups', []), ('is_superuser', True), ('is_staff', True),
                             ('is_active', False), ('password', 'other'), ('warehouse', 1)]:
            response = self.client.patch(self.url, {field: value, 'full_name': 'Wrong'}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.full_name, 'Target User')

    def test_user_without_account_loads_without_creating_then_saves_profile(self):
        self.account.delete()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertFalse(Account.objects.filter(user=self.target).exists())
        response = self.client.patch(self.url, {'full_name': 'New Profile'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Account.objects.get(user=self.target).full_name, 'New Profile')

    def test_multipart_image_replacement_and_empty_date(self):
        data = BytesIO(); Image.new('RGB', (2, 2)).save(data, format='PNG')
        image = SimpleUploadedFile('edit.png', data.getvalue(), content_type='image/png')
        response = self.client.patch(self.url, {'image': image, 'date_of_birth': '', 'gender': ''}, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.account.refresh_from_db()
        self.assertTrue(self.account.image.name.startswith('profile_pictures/'))
        self.assertEqual(response.data['image'], self.account.image.url)
        self.assertIsNone(self.account.date_of_birth)
        self.client.patch(self.url, {'full_name': 'Keep Image'}, format='json')
        previous = self.account.image.name
        self.account.refresh_from_db(); self.assertEqual(self.account.image.name, previous)
        invalid = SimpleUploadedFile('bad.png', b'not-image', content_type='image/png')
        response = self.client.patch(self.url, {'image': invalid, 'full_name': 'Wrong'}, format='multipart')
        self.assertEqual(response.status_code, 400, response.data)
        self.account.refresh_from_db(); self.assertEqual(self.account.full_name, 'Keep Image')

    def test_late_database_failure_rolls_back_login_and_profile(self):
        with patch('account.serializers.EditUserSerializer.save', side_effect=IntegrityError):
            response = self.client.patch(self.url, {'email': 'rollback@example.com'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.target.refresh_from_db(); self.account.refresh_from_db()
        self.assertEqual(self.target.username, 'target@example.com')
        self.assertEqual(self.account.email, 'target@example.com')

    def test_missing_records_methods_and_page_api_wiring(self):
        self.assertEqual(self.client.get(reverse('account:edit-user', args=[999999])).status_code, 404)
        self.assertEqual(self.client.get(reverse('account_pages:edit-user', args=[999999])).status_code, 404)
        for method in ['post', 'put', 'delete']:
            self.assertEqual(getattr(self.client, method)(self.url, {}, format='json').status_code, 405)
        page = self.client.get(self.page)
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'account/edit_user.html')
        self.assertContains(page, f'action="{self.url}"')
        self.assertContains(page, 'data-edit-user-form')
        self.assertContains(page, 'data-edit-fields disabled')
        self.assertNotContains(page, 'name="password"')
        self.assertContains(page, f'data-success-url="{reverse("account_pages:user-list")}"')
        listing = self.client.get(reverse('account_pages:user-list'))
        self.assertContains(listing, 'data-edit-user title="Edit user"')
        self.actor.user_permissions.clear(); self.actor.groups.clear()
        grant_permissions(self.actor, 'auth.view_user')
        self.assertNotContains(self.client.get(reverse('account_pages:user-list')), 'data-edit-user title="Edit user"')
