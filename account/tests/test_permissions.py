from .test_helpers import grant_permissions
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from account.models import Account
from account.serializers import PermissionListSerializer


User = get_user_model()


class PermissionListAPIViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='person@example.com')
        grant_permissions(self.user, 'auth.view_permission')
        self.client = APIClient(enforce_csrf_checks=True)
        self.url = reverse('account:permission-list')

    def test_regular_authenticated_user_can_list_all_permissions(self):
        self.client.force_login(self.user)
        self.assertFalse(self.user.is_staff)
        self.assertEqual(self.user.user_permissions.count(), 1)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), Permission.objects.count())
        self.assertGreater(len(response.data), 0)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.user.user_permissions.count(), 1)

    def test_anonymous_request_is_rejected(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_inactive_session_is_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_permission_response_includes_model_identity_and_custom_permissions(self):
        content_type = ContentType.objects.get_for_model(Account)
        permission = Permission.objects.create(
            content_type=content_type, codename='export_account', name='Can export account',
        )
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        row = next(row for row in response.data if row['id'] == permission.pk)
        self.assertEqual(row, {
            'id': permission.pk, 'name': 'Can export account', 'codename': 'export_account',
            'content_type': content_type.pk, 'app_label': 'account', 'model': 'account',
        })
        identities = [(row['app_label'], row['model'], row['codename']) for row in response.data]
        self.assertEqual(identities, sorted(identities))

    def test_post_is_not_allowed_and_does_not_create_or_assign_permissions(self):
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        before = Permission.objects.count()
        response = self.client.post(self.url, {
            'name': 'New permission', 'codename': 'new_permission',
        }, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Permission.objects.count(), before)
        self.assertEqual(self.user.user_permissions.count(), 1)

    def test_serializing_permission_models_uses_one_query(self):
        with self.assertNumQueries(1):
            data = PermissionListSerializer(
                Permission.objects.select_related('content_type').order_by('pk'), many=True,
            ).data
        self.assertGreater(len(data), 0)
