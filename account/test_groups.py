from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .serializers import GroupListSerializer


User = get_user_model()


class GroupListAPIViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='person@example.com')
        self.client = APIClient(enforce_csrf_checks=True)
        self.url = reverse('account:group-list')

    def test_regular_user_can_list_all_groups_and_permission_ids(self):
        teachers = Group.objects.create(name='Teachers')
        admins = Group.objects.create(name='Administrators')
        permission = Permission.objects.get(
            content_type__app_label='account', content_type__model='account', codename='view_account',
        )
        teachers.permissions.add(permission)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [
            {'id': admins.pk, 'name': 'Administrators', 'permissions': []},
            {'id': teachers.pk, 'name': 'Teachers', 'permissions': [permission.pk]},
        ])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(self.user.groups.exists())
        self.assertFalse(self.user.user_permissions.exists())

    def test_anonymous_and_inactive_sessions_are_rejected(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_no_groups_returns_empty_array(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_post_is_not_allowed_and_does_not_create_groups(self):
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        response = self.client.post(
            self.url, {'name': 'New group'}, format='json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 405)
        self.assertFalse(Group.objects.exists())

    def test_permissions_are_prefetched_for_all_groups(self):
        permission = Permission.objects.first()
        for name in ['Administrators', 'Teachers', 'Students']:
            group = Group.objects.create(name=name)
            group.permissions.add(permission)
        with self.assertNumQueries(2):
            data = GroupListSerializer(
                Group.objects.prefetch_related('permissions').order_by('name', 'pk'), many=True,
            ).data
        self.assertEqual(len(data), 3)
        for group in data:
            self.assertEqual(group['permissions'], [permission.pk])
