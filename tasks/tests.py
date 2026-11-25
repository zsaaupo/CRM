from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from tasks.models import SubTask, Task, TaskGroup


User = get_user_model()


class TaskManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(username='task_a', password='pass')
        cls.user_b = User.objects.create_user(username='task_b', password='pass')
        cls.user_c = User.objects.create_user(username='task_c', password='pass')
        cls.su = User.objects.create_superuser(username='task_su', email='tsu@example.com', password='pass')

    def test_f_tsk_1_visibility_seen_subtask_block_and_counters(self):
        client_b = APIClient()
        client_b.force_authenticate(user=self.user_b)
        client_a = APIClient()
        client_a.force_authenticate(user=self.user_a)
        client_c = APIClient()
        client_c.force_authenticate(user=self.user_c)

        grp = TaskGroup.objects.create(name='Ops', created_by=self.su)
        res = client_b.post(
            reverse('tasks:task-list'),
            {'title': 'Verify count', 'group': grp.pk, 'priority': 'high', 'assignees': [self.user_a.pk]},
            format='json',
        )
        self.assertEqual(res.status_code, 201, res.data)
        task_id = res.data['id']

        # B sees under created; A sees under responsibility and not_seen=1; C sees 0 tasks
        b_list = client_b.get(reverse('tasks:task-list'), {'created_by_me': '1'}).data
        self.assertEqual([t['id'] for t in b_list['results']], [task_id])
        self.assertEqual(b_list['counters']['created'], 1)

        a_resp = client_a.get(reverse('tasks:task-list'), {'responsibility': '1'}).data
        self.assertEqual([t['id'] for t in a_resp['results']], [task_id])
        self.assertEqual(a_resp['counters']['responsibility'], 1)
        self.assertEqual(a_resp['counters']['not_seen'], 1)

        c_list = client_c.get(reverse('tasks:task-list')).data
        self.assertEqual(c_list['results'], [])
        self.assertEqual(c_list['counters']['total'], 0)
        self.assertEqual(client_c.get(reverse('tasks:task-detail', args=[task_id])).status_code, 404)

        # First open by A decrements not_seen once (subsequent seen calls keep 0)
        seen1 = client_a.post(reverse('tasks:task-seen', args=[task_id]), {}, format='json')
        self.assertEqual(seen1.status_code, 200)
        self.assertTrue(seen1.data['first_open'])
        seen2 = client_a.post(reverse('tasks:task-seen', args=[task_id]), {}, format='json')
        self.assertFalse(seen2.data['first_open'])
        a_after_seen = client_a.get(reverse('tasks:task-list')).data
        self.assertEqual(a_after_seen['counters']['not_seen'], 0)

        # Open subtask blocks completion (D-54)
        sub_res = client_b.post(
            reverse('tasks:subtask-list'),
            {'task': task_id, 'title': 'Check shelf A', 'assignee': self.user_a.pk},
            format='json',
        )
        self.assertEqual(sub_res.status_code, 201)
        sub_id = sub_res.data['id']

        fail_complete = client_a.patch(
            reverse('tasks:task-detail', args=[task_id]),
            {'status': 'completed'},
            format='json',
        )
        self.assertEqual(fail_complete.status_code, 409)

        # Complete subtask -> parent completes -> completed_today increments
        self.assertEqual(
            client_a.patch(reverse('tasks:subtask-detail', args=[sub_id]), {'is_completed': True}, format='json').status_code,
            200,
        )
        ok_complete = client_a.patch(
            reverse('tasks:task-detail', args=[task_id]),
            {'status': 'completed'},
            format='json',
        )
        self.assertEqual(ok_complete.status_code, 200)
        rpt = client_b.get(reverse('tasks:report')).data
        self.assertEqual(rpt['counters']['completed_today'], 1)
        self.assertEqual(rpt['completion_rate'], 100.0)

        # User with tasks.view_all_tasks sees the task too
        perm = Permission.objects.get(content_type__app_label='tasks', codename='view_all_tasks')
        self.user_c.user_permissions.add(perm)
        fresh_c = User.objects.get(pk=self.user_c.pk)
        client_c.force_authenticate(user=fresh_c)
        self.assertEqual(len(client_c.get(reverse('tasks:task-list')).data['results']), 1)
