from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from common.exceptions import StateConflict
from .models import SubTask, Task, TaskComment, TaskGroup, TaskSeenState


def _block_open_subtasks() -> bool:
    try:
        from common.models import TaskSetting
        cfg = TaskSetting.objects.filter(pk=1).first()
        if cfg is not None:
            return bool(cfg.block_close_with_open_subtasks)
    except Exception:
        pass
    return True


def visible_tasks_queryset(user, qs=None):
    base = qs if qs is not None else Task.objects.all()
    if user.is_superuser or user.has_perm('tasks.view_all_tasks'):
        return base
    return base.filter(Q(created_by_id=user.pk) | Q(assignees=user) | Q(subtasks__assignee_id=user.pk)).distinct()


def responsibility_tasks_queryset(user, qs=None):
    base = visible_tasks_queryset(user, qs)
    return base.filter(Q(assignees=user) | Q(subtasks__assignee_id=user.pk)).distinct()


def mark_task_seen(task_id: int, user) -> tuple[TaskSeenState, bool]:
    task = visible_tasks_queryset(user).filter(pk=task_id).first()
    if task is None:
        raise NotFound('Task not found.')
    obj, created = TaskSeenState.objects.get_or_create(task=task, user=user, defaults={'seen_at': timezone.now()})
    return obj, created


def update_task_status(actor, task_id: int, new_status: str) -> Task:
    if new_status not in Task.Status.values:
        raise ValidationError({'status': 'Invalid task status.'})
    with transaction.atomic():
        task = visible_tasks_queryset(actor, Task.objects.select_for_update()).filter(pk=task_id).first()
        if task is None:
            raise NotFound('Task not found.')
        if new_status == Task.Status.COMPLETED and _block_open_subtasks():
            has_open = task.subtasks.filter(Q(is_completed=False) & ~Q(status=SubTask.Status.COMPLETED)).exists()
            if has_open:
                raise StateConflict('Complete all subtasks before marking this task completed.')
        task.status = new_status
        task.completed_at = timezone.now() if new_status == Task.Status.COMPLETED else None
        task.updated_by = actor
        task.modified_by = actor.get_username()
        task.save(update_fields=['status', 'completed_at', 'updated_by', 'modified_by', 'updated_at'])
        return task


def toggle_subtask(actor, subtask_id: int, *, is_completed: bool | None = None, new_status: str | None = None) -> SubTask:
    with transaction.atomic():
        sub = SubTask.objects.select_for_update().select_related('task').filter(pk=subtask_id).first()
        if sub is None or not visible_tasks_queryset(actor).filter(pk=sub.task_id).exists():
            raise NotFound('Subtask not found.')
        done = is_completed if is_completed is not None else (new_status == SubTask.Status.COMPLETED if new_status else not sub.is_completed)
        sub.is_completed = bool(done)
        sub.status = new_status or (SubTask.Status.COMPLETED if sub.is_completed else SubTask.Status.TODO)
        sub.completed_by = actor if sub.is_completed else None
        sub.completed_at = timezone.now() if sub.is_completed else None
        sub.modified_by = actor.get_username()
        sub.save(update_fields=['is_completed', 'status', 'completed_by', 'completed_at', 'modified_by', 'updated_at'])
        return sub


def compute_task_counters(user) -> dict:
    today = timezone.localdate()
    vis = visible_tasks_queryset(user)
    resp = responsibility_tasks_queryset(user, vis)
    created = vis.filter(created_by_id=user.pk)
    seen_ids = set(TaskSeenState.objects.filter(user_id=user.pk, task__in=vis).values_list('task_id', flat=True))
    not_seen_count = resp.exclude(pk__in=seen_ids).count()
    return {
        'total': vis.count(),
        'assigned': resp.count(),
        'responsibility': resp.count(),
        'created': created.count(),
        'not_seen': not_seen_count,
        'completed_today': vis.filter(status=Task.Status.COMPLETED, completed_at__date=today).count(),
        'overdue': vis.filter(due_date__lt=today).exclude(status__in=[Task.Status.COMPLETED, Task.Status.CANCELLED]).count(),
    }


def build_task_reports(user) -> dict:
    counters = compute_task_counters(user)
    vis = visible_tasks_queryset(user)
    total = counters['total']
    completed = vis.filter(status=Task.Status.COMPLETED).count()
    completion_rate = round((completed / total * 100), 2) if total else 0.0

    by_group = list(
        TaskGroup.objects.filter(tasks__in=vis)
        .annotate(task_count=Count('tasks', distinct=True))
        .values('id', 'name', 'task_count')
        .order_by('name')
    )
    by_status = list(vis.values('status').annotate(count=Count('pk', distinct=True)).order_by('status'))
    return {
        'counters': counters,
        'completed_total': completed,
        'completion_rate': completion_rate,
        'by_group': by_group,
        'by_status': by_status,
    }
