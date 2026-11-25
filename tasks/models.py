from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


class TaskGroup(TimeStampedModel):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=24, blank=True, default='#2563eb')
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_task_groups',
    )

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class Task(TimeStampedModel):
    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'
        URGENT = 'urgent', 'Urgent'

    class Status(models.TextChoices):
        TODO = 'todo', 'To Do'
        IN_PROGRESS = 'in_progress', 'In Progress'
        REVIEW = 'review', 'Review'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    group = models.ForeignKey(TaskGroup, on_delete=models.PROTECT, null=True, blank=True, related_name='tasks')
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TODO, db_index=True)
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_tasks',
    )
    assignees = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='assigned_tasks')
    attachment = models.FileField(upload_to='task_attachments/', blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='updated_tasks',
    )

    class Meta:
        ordering = ['-created_at', '-pk']
        permissions = [
            ('view_all_tasks', 'Can view all tasks regardless of assignment'),
            ('manage_groups', 'Can manage task groups'),
        ]

    def __str__(self):
        return self.title


class SubTask(TimeStampedModel):
    class Status(models.TextChoices):
        TODO = 'todo', 'To Do'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='subtasks')
    title = models.CharField(max_length=255)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='assigned_subtasks',
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TODO)
    is_completed = models.BooleanField(default=False)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='completed_subtasks',
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'pk']


class TaskSeenState(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='seen_states')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seen_tasks')
    seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['task', 'user'], name='tasks_seen_task_user_unique'),
        ]


class TaskComment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='task_comments')
    body = models.TextField()
    attachment = models.FileField(upload_to='task_comment_attachments/', blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['created_at', 'pk']
