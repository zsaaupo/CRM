from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import Subtask, Task, TaskGroup, TaskSeen, TaskStatus


def visible_tasks(user):
    rows = Task.objects.select_related('group', 'creator', 'assignee')
    if not user.has_perm('tasks.view_all_tasks'):
        delegated = Subtask.objects.filter(task_id=OuterRef('pk'), assignee=user)
        rows = rows.filter(Q(creator=user) | Q(assignee=user) | Exists(delegated))
    return rows.annotate(seen=Exists(TaskSeen.objects.filter(task_id=OuterRef('pk'), viewer=user)))


class TaskInput(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = ('title', 'description', 'assignee', 'group', 'priority', 'due_date', 'status')

    def validate_assignee(self, user):
        if not user.is_active:
            raise ValidationError('Choose an active assignee.')
        return user


class ChildInput(TaskInput):
    class Meta:
        model = Subtask
        fields = ('title', 'assignee', 'due_date', 'status')


class GroupInput(serializers.ModelSerializer):
    class Meta:
        model = TaskGroup
        fields = ('name', 'description')


def require(user, *permissions):
    if not user.has_perms(permissions):
        raise PermissionDenied()


@transaction.atomic
def save_task(user, data, pk=None):
    require(user, 'tasks.view_task', 'tasks.change_task' if pk else 'tasks.add_task')
    task = get_object_or_404(visible_tasks(user).select_for_update(of=('self',)), pk=pk) if pk else None
    manages = not task or task.creator_id == user.pk or user.has_perm('tasks.view_all_tasks')
    if not manages and (task.assignee_id != user.pk or set(data) - {'status'}):
        raise PermissionDenied('Only the creator or a task manager can edit the assignment.')
    form = TaskInput(task, data=data, partial=task is not None)
    if data.get('group'):
        require(user, 'tasks.view_taskgroup')
    form.is_valid(raise_exception=True)
    changes = {'modified_by': user.get_username()}
    status = form.validated_data.get('status', task.status if task else TaskStatus.TODO)
    if status == TaskStatus.DONE:
        changes['completed_at'] = task.completed_at if task and task.completed_at else timezone.now()
    else:
        changes['completed_at'] = None
    if not task:
        changes.update(creator=user, create_by=user.get_username())
    elif 'assignee' in form.validated_data and task.assignee_id != form.validated_data['assignee'].pk:
        TaskSeen.objects.filter(task=task, viewer=form.validated_data['assignee']).delete()
    return form.save(**changes)


@transaction.atomic
def save_subtask(user, pk, data, child_pk=None):
    require(user, 'tasks.view_task', 'tasks.change_subtask' if child_pk else 'tasks.add_subtask')
    task = get_object_or_404(visible_tasks(user).select_for_update(of=('self',)), pk=pk)
    child = get_object_or_404(task.subtasks.select_for_update(), pk=child_pk) if child_pk else None
    manages = task.creator_id == user.pk or user.has_perm('tasks.view_all_tasks')
    if not manages and (not child or child.assignee_id != user.pk or set(data) - {'status'}):
        raise PermissionDenied()
    form = ChildInput(child, data=data, partial=child is not None)
    form.is_valid(raise_exception=True)
    audit = {'modified_by': user.get_username()}
    if not child:
        audit['create_by'] = user.get_username()
    assigned = form.validated_data.get('assignee')
    if assigned and (not child or child.assignee_id != assigned.pk):
        TaskSeen.objects.filter(task=task, viewer=assigned).delete()
    return form.save(task=task, **audit)


class Filters(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=200)
    status = serializers.ChoiceField(choices=TaskStatus.choices, required=False, allow_blank=True)
    group = serializers.IntegerField(required=False, min_value=1)
    assignee = serializers.IntegerField(required=False, min_value=1)


def filtered(user, query, responsibility=False):
    form = Filters(data={k: v for k, v in query.items() if v != ''})
    form.is_valid(raise_exception=True)
    values = form.validated_data
    rows = visible_tasks(user)
    if responsibility:
        rows = rows.filter(Q(assignee=user) | Exists(Subtask.objects.filter(task_id=OuterRef('pk'), assignee=user)))
    if values.get('q'):
        rows = rows.filter(Q(title__icontains=values['q']) | Q(description__icontains=values['q']))
    for field in ('group', 'assignee', 'status'):
        if values.get(field):
            rows = rows.filter(**{field: values[field]})
    return rows


def counters(rows, user):
    today = timezone.localdate()
    return rows.aggregate(total=Count('id'), assigned=Count('id', filter=Q(assignee=user)),
                          created=Count('id', filter=Q(creator=user)), unseen=Count('id', filter=Q(seen=False)),
                          completed_today=Count('id', filter=Q(completed_at__date=today)),
                          overdue=Count('id', filter=Q(due_date__lt=today) & ~Q(status__in=[TaskStatus.DONE, TaskStatus.CANCELLED])))
