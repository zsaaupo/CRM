from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


class TaskStatus(models.TextChoices):
    TODO = 'todo', 'To do'
    ACTIVE = 'in_progress', 'In progress'
    REVIEW = 'review', 'In review'
    DONE = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'


class TaskGroup(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Task(TimeStampedModel):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_tasks')
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_tasks')
    group = models.ForeignKey(TaskGroup, on_delete=models.PROTECT, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=TaskStatus.choices, default=TaskStatus.TODO)
    priority = models.CharField(max_length=10, choices=[(v, v.title()) for v in ('low','normal','high','urgent')], default='normal')
    completed_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        ordering = ('-created_at', '-pk')
        permissions = [('view_all_tasks', 'View tasks across all users')]
        indexes = [models.Index(fields=['assignee', 'status', 'due_date'])]

    def __str__(self):
        return self.title


class Subtask(TimeStampedModel):
    task = models.ForeignKey(Task, related_name='subtasks', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=TaskStatus.choices, default=TaskStatus.TODO)


class TaskSeen(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='read_receipts')
    viewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['task', 'viewer'], name='task_seen_once_per_viewer')]
