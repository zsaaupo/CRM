from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from account.serializers import GroupPermissionsSerializer


User = get_user_model()


class GroupPermissionsAPIViewTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='admin@example.com', email='admin@example.com', password='Admin!47Strong',
        )
        self.member = User.objects.create_user(username='member@example.com')
        self.group = Group.objects.create(name='Teachers')
        self.member.groups.add(self.group)
        account_permissions = Permission.objects.filter(
            content_type__app_label='account', content_type__model='account',
        )
        self.old = account_permissions.get(codename='view_account')
        self.new = account_permissions.get(codename='change_account')
        self.custom = Permission.objects.create(
            content_type=self.old.content_type, codename='export_account', name='Can export account',
        )
        self.group.permissions.add(self.old)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.superuser)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.url = reverse('account:group-permissions', args=[self.group.pk])

    def put(self, data, url=None):
        return self.client.put(
            url or self.url, data, format='json', HTTP_X_CSRFTOKEN=self.token,
        )

    def test_superuser_replaces_the_complete_permission_selection(self):
        response = self.put({'permissions': [self.new.pk, self.custom.pk]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], self.group.pk)
        self.assertEqual(response.data['name'], 'Teachers')
        self.assertCountEqual(response.data['permissions'], [self.new.pk, self.custom.pk])
        self.assertCountEqual(self.group.permissions.all(), [self.new, self.custom])
        self.assertTrue(self.member.groups.filter(pk=self.group.pk).exists())
        self.assertIn('no-store', response['Cache-Control'])

    def test_repeated_requests_are_idempotent_and_duplicate_ids_are_removed(self):
        data = {'permissions': [self.new.pk, self.new.pk, self.custom.pk]}
        first = self.put(data)
        second = self.put(data)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data, second.data)
        self.assertEqual(self.group.permissions.count(), 2)

    def test_empty_list_explicitly_clears_all_permissions(self):
        response = self.put({'permissions': []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['permissions'], [])
        self.assertFalse(self.group.permissions.exists())
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())
        self.assertTrue(Permission.objects.filter(pk=self.old.pk).exists())

    def test_invalid_payloads_leave_current_permissions_unchanged(self):
        missing_id = Permission.objects.order_by('-pk').first().pk + 1
        cases = [
            {}, {'permissions': None}, {'permissions': '1,2'}, {'permissions': {}},
            {'permissions': [None]}, {'permissions': [True]}, {'permissions': ['invalid']},
            {'permissions': [-1]}, {'permissions': [0]}, {'permissions': [1.5]},
            {'permissions': [self.new.pk, missing_id]},
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.put(data)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('permissions', response.data)
                self.assertEqual(list(self.group.permissions.all()), [self.old])

    def test_unknown_group_returns_404(self):
        response = self.put(
            {'permissions': [self.new.pk]},
            reverse('account:group-permissions', args=[99999]),
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_user_cannot_assign_permissions(self):
        response = APIClient(enforce_csrf_checks=True).put(
            self.url, {'permissions': [self.new.pk]}, format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.group.permissions.all()), [self.old])

    def test_regular_user_and_staff_cannot_assign_without_access_management_permission(self):
        for is_staff in [False, True]:
            with self.subTest(is_staff=is_staff):
                self.member.is_staff = is_staff
                self.member.save(update_fields=['is_staff'])
                client = APIClient(enforce_csrf_checks=True)
                client.force_login(self.member)
                token = client.get(reverse('account:login')).data['csrfToken']
                response = client.put(self.url, {'permissions': [self.new.pk]}, format='json', HTTP_X_CSRFTOKEN=token)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.group.permissions.all()), [self.old])

    def test_inactive_superuser_cannot_assign(self):
        self.superuser.is_active = False
        self.superuser.save(update_fields=['is_active'])
        self.assertEqual(self.put({'permissions': []}).status_code, 403)
        self.assertEqual(list(self.group.permissions.all()), [self.old])

    def test_put_requires_valid_csrf(self):
        missing = self.client.put(self.url, {'permissions': []}, format='json')
        invalid = self.client.put(
            self.url, {'permissions': []}, format='json', HTTP_X_CSRFTOKEN='invalid-token',
        )
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(list(self.group.permissions.all()), [self.old])

    def test_replacement_does_not_rename_or_modify_other_groups_or_direct_user_permissions(self):
        other_group = Group.objects.create(name='Other role')
        other_group.permissions.add(self.old)
        self.member.user_permissions.add(self.custom)
        response = self.put({'permissions': [self.new.pk], 'name': 'Renamed', 'is_superuser': True})
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(self.group.name, 'Teachers')
        self.assertEqual(list(other_group.permissions.all()), [self.old])
        self.assertEqual(list(self.member.user_permissions.all()), [self.custom])
        self.assertFalse(self.member.is_superuser)
        self.assertTrue(self.member.groups.filter(pk=self.group.pk).exists())

    def test_group_members_inherit_saved_permissions(self):
        self.put({'permissions': [self.new.pk]})
        member = User.objects.get(pk=self.member.pk)
        self.assertTrue(member.has_perm('account.change_account'))
        self.assertFalse(member.has_perm('account.view_account'))
        self.put({'permissions': []})
        member = User.objects.get(pk=self.member.pk)
        self.assertFalse(member.has_perm('account.change_account'))

    def test_failure_during_replacement_rolls_back_changes(self):
        def fail_after_clear(manager, objects):
            manager.clear()
            raise RuntimeError('Simulated failure during replacement')

        with patch.object(type(self.group.permissions), 'set', new=fail_after_clear):
            with self.assertRaises(RuntimeError):
                self.put({'permissions': [self.new.pk]})
        self.assertEqual(list(self.group.permissions.all()), [self.old])

    def test_only_put_and_options_are_supported(self):
        for method in ['get', 'post', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url, HTTP_X_CSRFTOKEN=self.token)
                self.assertEqual(response.status_code, 405)
        self.assertEqual(list(self.group.permissions.all()), [self.old])

    def test_permission_id_validation_uses_one_database_query(self):
        serializer = GroupPermissionsSerializer(data={
            'permissions': [self.old.pk, self.new.pk, self.custom.pk, self.new.pk],
        })
        with self.assertNumQueries(1):
            self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['permissions'], sorted({self.old.pk, self.new.pk, self.custom.pk}))
