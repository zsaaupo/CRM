from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient


User = get_user_model()


class UserGroupTestSetup:
    def setUp(self):
        self.caller = User.objects.create_user(username='manager@example.com')
        self.target = User.objects.create_user(username='member@example.com')
        self.old = Group.objects.create(name='Teachers')
        self.new = Group.objects.create(name='Sales')
        self.target.groups.add(self.old)
        self.permission = Permission.objects.get(
            content_type__app_label='account', codename='view_account',
        )
        self.new.permissions.add(self.permission)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.caller)
        self.token = self.client.get(reverse('account:login')).data['csrfToken']
        self.url = reverse('account:user-groups', args=[self.target.pk])


class UserGroupsAPIViewTests(UserGroupTestSetup, TestCase):
    def put(self, data, url=None):
        return self.client.put(
            url or self.url, data, format='json', HTTP_X_CSRFTOKEN=self.token,
        )

    def test_get_returns_current_groups_without_changing_them(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'user_id': self.target.pk, 'groups': [self.old.pk]})
        self.assertIn('no-store', response['Cache-Control'])

    def test_authenticated_user_can_replace_groups_and_inherit_permissions(self):
        response = self.put({'group': self.new.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {'user': self.target.pk, 'group': self.new.pk})
        self.assertIn('no-store', response['Cache-Control'])
        user = User.objects.get(pk=self.target.pk)
        self.assertTrue(user.has_perm('account.view_account'))
        self.assertEqual(list(user.groups.all()), [self.new])
        self.assertFalse(self.caller.groups.exists())

    def test_repeated_assignment_leaves_exactly_one_group(self):
        payload = {'group': self.new.pk}
        for _ in range(2):
            response = self.put(payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['group'], self.new.pk)
            self.assertEqual(list(self.target.groups.all()), [self.new])
        response = self.put({'group': self.old.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['group'], self.old.pk)
        self.assertEqual(list(self.target.groups.all()), [self.old])
        self.assertFalse(User.objects.get(pk=self.target.pk).has_perm('account.view_account'))

    def test_invalid_payloads_preserve_assignments(self):
        cases = [{}, {'group': None}, {'group': []}, {'group': {}},
                 {'group': [self.old.pk, self.new.pk]}, {'group': True}, {'group': 0},
                 {'group': -1}, {'group': 1.5}, {'group': 'invalid'},
                 {'group': self.new.pk + 999}, {'groups': [self.old.pk, self.new.pk]},
                 {'groups': []}, {'group': self.new.pk, 'groups': [self.old.pk]}]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.put(payload)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertTrue('group' in response.data or 'groups' in response.data)
                self.assertEqual(list(self.target.groups.all()), [self.old])

    def test_anonymous_and_inactive_callers_are_rejected(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get(self.url).status_code, 403)
        self.assertEqual(anonymous.put(self.url, {'group': self.new.pk}, format='json').status_code, 403)
        self.caller.is_active = False
        self.caller.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.put({'group': self.new.pk}).status_code, 403)
        self.assertEqual(list(self.target.groups.all()), [self.old])

    def test_put_requires_csrf(self):
        for headers in [{}, {'HTTP_X_CSRFTOKEN': 'invalid-token'}]:
            response = self.client.put(self.url, {'group': self.new.pk}, format='json', **headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.target.groups.all()), [self.old])

    def test_unknown_user_returns_404(self):
        url = reverse('account:user-groups', args=[self.target.pk + 999])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.put({'group': self.new.pk}, url=url).status_code, 404)

    def test_assignment_does_not_modify_flags_direct_permissions_or_group_permissions(self):
        self.target.user_permissions.add(self.permission)
        password = self.target.password
        response = self.put({'group': self.new.pk, 'is_superuser': True,
                             'is_staff': True, 'is_active': False, 'password': 'changed',
                             'user_permissions': []})
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_superuser)
        self.assertFalse(self.target.is_staff)
        self.assertTrue(self.target.is_active)
        self.assertEqual(self.target.password, password)
        self.assertEqual(list(self.target.user_permissions.all()), [self.permission])
        self.assertEqual(list(self.new.permissions.all()), [self.permission])

    def test_failure_rolls_back_membership_changes(self):
        def fail_after_clear(manager, objects):
            manager.clear()
            raise RuntimeError('Simulated assignment failure')

        with patch.object(type(self.target.groups), 'set', new=fail_after_clear):
            with self.assertRaises(RuntimeError):
                self.put({'group': self.new.pk})
        self.assertEqual(list(self.target.groups.all()), [self.old])

    def test_other_write_methods_are_not_supported(self):
        for method in ['post', 'patch', 'delete']:
            response = getattr(self.client, method)(
                self.url, {'group': self.new.pk}, format='json', HTTP_X_CSRFTOKEN=self.token,
            )
            self.assertEqual(response.status_code, 405)
        self.assertEqual(list(self.target.groups.all()), [self.old])


class AssignGroupAPIViewTests(UserGroupTestSetup, TestCase):
    def setUp(self):
        super().setUp()
        self.assign_url = reverse('account:assign-group')

    def assign(self, data, client=None, **headers):
        return (client or self.client).post(
            self.assign_url, data, format='json', **headers,
        )

    def test_post_assigns_one_group_and_replaces_previous_assignment(self):
        for group in [self.new, self.new, self.old]:
            response = self.assign(
                {'user': self.target.pk, 'group': group.pk}, HTTP_X_CSRFTOKEN=self.token,
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data, {'user': self.target.pk, 'group': group.pk})
            self.assertEqual(list(self.target.groups.all()), [group])
            self.assertIn('no-store', response['Cache-Control'])

    def test_post_rejects_missing_invalid_or_multiple_ids(self):
        cases = [{}, {'user': self.target.pk}, {'group': self.new.pk},
                 {'user': [self.caller.pk, self.target.pk], 'group': self.new.pk},
                 {'user': self.target.pk, 'group': [self.old.pk, self.new.pk]},
                 {'user': self.target.pk, 'group': self.new.pk + 999},
                 {'user': self.target.pk, 'group': None},
                 {'user': self.target.pk, 'groups': [self.new.pk]},
                 {'user': self.target.pk, 'group': self.new.pk, 'groups': [self.old.pk]}]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.assign(payload, HTTP_X_CSRFTOKEN=self.token)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(list(self.target.groups.all()), [self.old])

    def test_post_missing_target_returns_404(self):
        response = self.assign(
            {'user': self.target.pk + 999, 'group': self.new.pk},
            HTTP_X_CSRFTOKEN=self.token,
        )
        self.assertEqual(response.status_code, 404)

    def test_post_requires_active_session_and_csrf(self):
        payload = {'user': self.target.pk, 'group': self.new.pk}
        self.assertEqual(self.assign(payload, client=APIClient()).status_code, 403)
        self.assertEqual(self.assign(payload).status_code, 403)
        self.assertEqual(self.assign(payload, HTTP_X_CSRFTOKEN='invalid-token').status_code, 403)
        self.caller.is_active = False
        self.caller.save(update_fields=['is_active'])
        self.assertEqual(self.assign(payload, HTTP_X_CSRFTOKEN=self.token).status_code, 403)
        self.assertEqual(list(self.target.groups.all()), [self.old])

    def test_post_failure_rolls_back_previous_group(self):
        def fail_after_clear(manager, objects):
            manager.clear()
            raise RuntimeError('Simulated assignment failure')

        with patch.object(type(self.target.groups), 'set', new=fail_after_clear):
            with self.assertRaises(RuntimeError):
                self.assign(
                    {'user': self.target.pk, 'group': self.new.pk},
                    HTTP_X_CSRFTOKEN=self.token,
                )
        self.assertEqual(list(self.target.groups.all()), [self.old])
