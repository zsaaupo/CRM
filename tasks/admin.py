from django.contrib import admin

from common.admin import AuditedAdmin
from .models import SubTask, Task, TaskComment, TaskGroup, TaskSeenState


@admin.register(TaskGroup)
class TaskGroupAdmin(AuditedAdmin):
    list_display = ['name', 'color', 'is_active', 'created_by']
    search_fields = ['name']


@admin.register(Task)
class TaskAdmin(AuditedAdmin):
    list_display = ['title', 'group', 'priority', 'status', 'due_date', 'created_by', 'completed_at']
    list_filter = ['status', 'priority', 'group']
    search_fields = ['title', 'description']


@admin.register(SubTask)
class SubTaskAdmin(AuditedAdmin):
    list_display = ['task', 'title', 'assignee', 'status', 'is_completed', 'due_date']
    list_filter = ['status', 'is_completed']


@admin.register(TaskSeenState)
class TaskSeenStateAdmin(admin.ModelAdmin):
    list_display = ['task', 'user', 'seen_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = ['task', 'author', 'created_at']
