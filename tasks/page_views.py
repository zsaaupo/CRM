from django.shortcuts import get_object_or_404
from account.page_views import ProtectedAccountPageView
from .services import build_task_reports, compute_task_counters, visible_tasks_queryset


class TaskPageMixin(ProtectedAccountPageView):
    permission_required = ()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_section'] = 'tasks'
        ctx['task_counters'] = compute_task_counters(self.request.user)
        return ctx


class TaskListPageView(TaskPageMixin):
    template_name = 'tasks/list.html'
    view_mode = 'all'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_label'] = 'Responsibility' if self.view_mode == 'responsibility' else 'All Tasks'
        ctx['view_mode'] = self.view_mode
        return ctx


class TaskGroupsPageView(TaskPageMixin):
    template_name = 'tasks/list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_label'] = 'Task Groups'
        ctx['view_mode'] = 'groups'
        return ctx


class TaskDetailPageView(TaskPageMixin):
    template_name = 'tasks/list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        task = get_object_or_404(visible_tasks_queryset(self.request.user), pk=self.kwargs['pk'])
        ctx['page_label'] = task.title
        ctx['task'] = task
        ctx['view_mode'] = 'detail'
        return ctx


class TaskReportsPageView(TaskPageMixin):
    template_name = 'tasks/list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_label'] = 'Task Reports'
        ctx['view_mode'] = 'reports'
        ctx['report_data'] = build_task_reports(self.request.user)
        return ctx
