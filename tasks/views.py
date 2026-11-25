from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from account.page_views import ProtectedAccountPageView
from common.view import BaseAPIMixin
from .models import Task, TaskGroup, TaskSeen, TaskStatus
from .services import GroupInput, counters, filtered, require, save_subtask, save_task, visible_tasks


class TaskResource:
    area = 'list'
    required_permissions = ('tasks.view_task',)

    def read(self):
        user = self.request.user
        if self.area == 'detail':
            task = get_object_or_404(visible_tasks(user), pk=self.kwargs['pk'])
            TaskSeen.objects.get_or_create(task=task, viewer=user)
            children = task.subtasks.select_related('assignee') if user.has_perm('tasks.view_subtask') else []
            return dict(task=task, children=children)
        if self.area == 'groups':
            require(user, 'tasks.view_taskgroup')
            groups = TaskGroup.objects.annotate(task_count=Count('task', filter=Q(task__in=visible_tasks(user).values('pk'))))
            return dict(groups=groups.order_by('name'))
        rows = filtered(user, self.request.GET, self.area == 'responsibility')
        page = Paginator(rows, 30).get_page(self.request.GET.get('page'))
        result = dict(records=page, counts=counters(rows, user))
        if self.area == 'report':
            result['workload'] = list(rows.order_by().values('assignee__username', 'group__name').annotate(
                total=Count('id'), completed=Count('id', filter=Q(status=TaskStatus.DONE))).order_by('assignee__username', 'group__name'))
        return result

    def write(self, data):
        user = self.request.user
        pk = self.kwargs.get('pk')
        if self.area == 'groups':
            require(user, 'tasks.add_taskgroup', 'tasks.view_taskgroup')
            form = GroupInput(data=data)
            form.is_valid(raise_exception=True)
            return form.save(create_by=user.get_username(), modified_by=user.get_username())
        if self.area == 'subtask':
            return save_subtask(user, pk, data, self.kwargs.get('child_pk'))
        return save_task(user, data, pk)


def task_row(task):
    return dict(id=task.pk, title=task.title, description=task.description, status=task.status, priority=task.priority,
                creator=task.creator_id, assignee=task.assignee_id, group=task.group_id, due_date=task.due_date,
                completed_at=task.completed_at, seen=task.seen)


class TaskAPI(TaskResource, BaseAPIMixin, APIView):
    def get(self, request, **kwargs):
        result = self.read()
        if 'task' in result:
            row = task_row(result['task'])
            row['seen'] = True
            row['subtasks'] = [dict(id=s.pk, title=s.title, assignee=s.assignee_id, due_date=s.due_date,
                                    status=s.status) for s in result['children']]
            return Response(row)
        if 'groups' in result:
            return Response(list(result['groups'].values('id', 'name', 'description', 'create_by', 'task_count')))
        page = result['records']
        result.pop('records')
        return Response(dict(**result, page=page.number, pages=page.paginator.num_pages,
                             results=[task_row(task) for task in page]))

    def post(self, request, **kwargs):
        record = self.write(request.data)
        return Response({'id': record.pk}, status=200 if self.area == 'detail' or 'child_pk' in kwargs else 201)


class TaskPage(TaskResource, ProtectedAccountPageView):
    permission_required = ('tasks.view_task',)
    template_name = 'tasks/workspace.html'

    def has_permission(self):
        return super().has_permission() and (self.area != 'groups' or self.request.user.has_perm('tasks.view_taskgroup'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        try:
            data = self.read()
        except ValidationError as exc:
            data = {'errors': exc.detail}
        context.update(data)
        context.update(page_section='tasks', area=self.area, statuses=TaskStatus.choices,
                       priorities=Task._meta.get_field('priority').choices)
        if user.has_perm('tasks.add_task') or user.has_perm('tasks.change_task') or user.has_perm('tasks.add_subtask'):
            context['users'] = get_user_model().objects.filter(is_active=True).order_by('username').only('username')
            context['group_options'] = TaskGroup.objects.order_by('name') if user.has_perm('tasks.view_taskgroup') else []
        if 'task' in data:
            task = data['task']
            context['manages'] = task.creator_id == user.pk or user.has_perm('tasks.view_all_tasks')
        return context

    def post(self, request, **kwargs):
        data = request.POST.dict()
        data.pop('csrfmiddlewaretoken', None)
        for field in ('due_date', 'group'):
            if field in data and not data[field]:
                data[field] = None
        try:
            result = self.write(data)
        except ValidationError as exc:
            if self.area == 'subtask':
                self.area = 'detail'
            return self.render_to_response(self.get_context_data(errors=exc.detail), status=400)
        except PermissionDenied as exc:
            from django.core.exceptions import PermissionDenied as PageDenied
            raise PageDenied(str(exc)) from exc
        if self.area == 'groups':
            return redirect('tasks:groups')
        return redirect('tasks:detail', pk=kwargs.get('pk', result.pk))
