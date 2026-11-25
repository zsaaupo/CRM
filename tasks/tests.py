from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import TaskSeen


class TaskWorkflowTests(TestCase):
    def test_visibility_seen_state_assignment_and_report(self):
        users = get_user_model().objects
        manager = users.create_superuser(username='manager')
        worker = users.create_user(username='worker')
        outsider = users.create_user(username='outsider')
        perms = Permission.objects.filter(content_type__app_label='tasks', codename__in=['view_task', 'change_task'])
        for person in [worker, outsider]:
            person.user_permissions.add(*perms)
        client = APIClient()
        client.force_login(manager)
        result = client.post(reverse('tasks:list-api'), {'assignee': worker.pk, 'title': 'Review invoice'}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        detail = reverse('tasks:detail-api', args=[result.data['id']])
        client.force_login(outsider)
        self.assertEqual(client.get(detail).status_code, 404)
        self.assertEqual(client.get(reverse('tasks:report-api')).data['counts']['total'], 0)
        client.force_login(worker)
        responsibility = reverse('tasks:responsibility-api')
        self.assertEqual(client.get(responsibility).data['counts']['unseen'], 1)
        self.assertEqual(client.get(detail).status_code, 200)
        self.assertEqual(client.get(detail).status_code, 200)
        self.assertEqual(TaskSeen.objects.count(), 1)
        self.assertEqual(client.get(responsibility).data['counts']['unseen'], 0)
        self.assertEqual(client.post(detail, {'assignee': outsider.pk}, format='json').status_code, 403)
        self.assertEqual(client.post(detail, {'status': 'completed'}, format='json').status_code, 200)
        report = client.get(reverse('tasks:report-api')).data
        self.assertEqual(report['counts']['completed_today'], 1)
        self.assertEqual(report['workload'][0]['completed'], 1)
        self.assertContains(client.get(reverse('tasks:report')), 'Review invoice')
        self.assertContains(client.get(reverse('tasks:detail', args=[result.data['id']])), 'Completed')
