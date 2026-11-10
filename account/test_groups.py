from .test_helpers import grant_permissions
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .serializers import GroupListSerializer


User = get_user_model()


class GroupListAPIViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='person@example.com')
        grant_permissions(self.user, 'auth.view_group', 'auth.add_group')
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
        self.assertEqual(self.user.user_permissions.count(), 2)

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

    def test_regular_user_can_create_group(self):
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        response = self.client.post(
            self.url, {'name': '  New group  '}, format='json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201, response.data)
        group = Group.objects.get(name='New group')
        self.assertEqual(response.data, {'id': group.pk, 'name': group.name, 'permissions': []})
        self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(self.user.groups.exists())

    def test_anonymous_and_inactive_users_cannot_create_groups(self):
        self.assertEqual(self.client.post(self.url, {'name': 'Denied'}, format='json').status_code, 403)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        response = self.client.post(
            self.url, {'name': 'Denied'}, format='json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Group.objects.exists())

    def test_creation_requires_valid_csrf(self):
        self.client.force_login(self.user)
        self.client.get(reverse('account:login'))
        for headers in [{}, {'HTTP_X_CSRFTOKEN': 'invalid-token'}]:
            response = self.client.post(self.url, {'name': 'Denied'}, format='json', **headers)
            self.assertEqual(response.status_code, 403)
        self.assertFalse(Group.objects.exists())

    def test_invalid_names_and_duplicates_are_rejected(self):
        Group.objects.create(name='Teachers')
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        for payload in [{}, {'name': None}, {'name': ''}, {'name': '   '},
                        {'name': 'x' * 151}, {'name': 'Teachers'}, {'name': ' Teachers '}]:
            with self.subTest(payload=payload):
                response = self.client.post(
                    self.url, payload, format='json', HTTP_X_CSRFTOKEN=token,
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('name', response.data)
        self.assertEqual(Group.objects.count(), 1)

    def test_creation_does_not_assign_permissions_or_members(self):
        permission = Permission.objects.first()
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        response = self.client.post(
            self.url, {'name': 'Teachers', 'id': 99999, 'permissions': [permission.pk],
                       'user_set': [self.user.pk]},
            format='json', HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201, response.data)
        group = Group.objects.get(pk=response.data['id'])
        self.assertNotEqual(group.pk, 99999)
        self.assertFalse(group.permissions.exists())
        self.assertFalse(group.user_set.exists())

    def test_concurrent_duplicate_returns_validation_error(self):
        Group.objects.create(name='Teachers')
        self.client.force_login(self.user)
        token = self.client.get(reverse('account:login')).data['csrfToken']
        with (
            patch('rest_framework.validators.UniqueValidator.__call__'),
            patch('account.serializers.GroupDetailSerializer.create', side_effect=IntegrityError),
        ):
            response = self.client.post(
                self.url, {'name': 'Teachers'}, format='json', HTTP_X_CSRFTOKEN=token,
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)
        self.assertEqual(Group.objects.count(), 1)

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
