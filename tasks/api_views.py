from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.serializers import AUDIT_FIELDS, CatalogSerializer
from common.view import BaseAPIMixin, BaseDetailAPIView
from .models import SubTask, Task, TaskComment, TaskGroup
from .services import (
    build_task_reports, compute_task_counters, mark_task_seen,
    responsibility_tasks_queryset, toggle_subtask, update_task_status, visible_tasks_queryset,
)


User = get_user_model()


class TaskGroupSerializer(CatalogSerializer):
    task_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = TaskGroup
        fields = ['id', 'name', 'description', 'color', 'is_active', 'created_by', 'task_count', *AUDIT_FIELDS[1:]]
        read_only_fields = [*AUDIT_FIELDS, 'created_by']


class SubTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubTask
        fields = [
            'id', 'task', 'title', 'assignee', 'due_date', 'status',
            'is_completed', 'completed_by', 'completed_at', 'sort_order',
        ]
        read_only_fields = ['completed_by', 'completed_at']


class TaskCommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author.username', read_only=True)

    class Meta:
        model = TaskComment
        fields = ['id', 'task', 'author', 'author_username', 'body', 'attachment', 'created_at']
        read_only_fields = ['author', 'created_at']


class TaskSerializer(serializers.ModelSerializer):
    assignees = serializers.PrimaryKeyRelatedField(many=True, queryset=User.objects.all(), required=False)
    subtasks = SubTaskSerializer(many=True, read_only=True)
    comments = TaskCommentSerializer(many=True, read_only=True)

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'description', 'group', 'priority', 'status',
            'start_date', 'due_date', 'created_by', 'assignees', 'attachment',
            'completed_at', 'updated_by', 'subtasks', 'comments', *AUDIT_FIELDS[1:],
        ]
        read_only_fields = [*AUDIT_FIELDS, 'created_by', 'completed_at', 'updated_by']


class TaskGroupListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        vis = visible_tasks_queryset(request.user)
        qs = TaskGroup.objects.annotate(
            task_count=Count('tasks', filter=Q(tasks__in=vis), distinct=True),
        ).order_by('name', 'pk')
        return Response({'results': TaskGroupSerializer(qs, many=True).data})

    def post(self, request):
        if not (request.user.is_superuser or request.user.has_perm('tasks.manage_groups') or request.user.has_perm('tasks.add_taskgroup')):
            return Response({'detail': 'Permission tasks.manage_groups required.'}, status=status.HTTP_403_FORBIDDEN)
        ser = TaskGroupSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        ser.save(created_by=request.user, create_by=request.user.get_username(), modified_by=request.user.get_username())
        return Response(ser.data, status=status.HTTP_201_CREATED)


class TaskListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        if request.query_params.get('responsibility') in ('1', 'true'):
            qs = responsibility_tasks_queryset(request.user)
        elif request.query_params.get('created_by_me') in ('1', 'true'):
            qs = visible_tasks_queryset(request.user).filter(created_by_id=request.user.pk)
        else:
            qs = visible_tasks_queryset(request.user)

        for param, field in [('status', 'status'), ('priority', 'priority'), ('group', 'group_id')]:
            if request.query_params.get(param):
                qs = qs.filter(**{field: request.query_params[param]})
        if request.query_params.get('assignee'):
            qs = qs.filter(assignees__pk=int(request.query_params['assignee'])).distinct()
        if request.query_params.get('search'):
            q = request.query_params['search'].strip()
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        return Response({
            'counters': compute_task_counters(request.user),
            'results': TaskSerializer(qs.prefetch_related('assignees', 'subtasks', 'comments')[:100], many=True).data,
        })

    @transaction.atomic
    def post(self, request):
        ser = TaskSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        username = request.user.get_username()
        task = ser.save(created_by=request.user, updated_by=request.user, create_by=username, modified_by=username)
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)


class TaskDetailAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'put', 'delete', 'head', 'options']

    def _get(self, request, pk):
        task = visible_tasks_queryset(request.user).filter(pk=pk).first()
        if task is None:
            return get_object_or_404(Task.objects.none(), pk=pk)
        return task

    def get(self, request, pk):
        task = self._get(request, pk)
        return Response(TaskSerializer(task).data)

    @transaction.atomic
    def patch(self, request, pk):
        task = self._get(request, pk)
        if 'status' in request.data and len(request.data) == 1:
            updated = update_task_status(request.user, task.pk, request.data['status'])
            return Response(TaskSerializer(updated).data)
        new_status = request.data.get('status')
        ser = TaskSerializer(task, data=request.data, partial=True, context={'request': request})
        ser.is_valid(raise_exception=True)
        updated = ser.save(updated_by=request.user, modified_by=request.user.get_username())
        if new_status:
            updated = update_task_status(request.user, updated.pk, new_status)
        return Response(TaskSerializer(updated).data)


class TaskSeenAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        state, created = mark_task_seen(pk, request.user)
        return Response({'task': pk, 'seen_at': state.seen_at, 'first_open': created})


class SubTaskListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        vis = visible_tasks_queryset(request.user)
        qs = SubTask.objects.filter(task__in=vis)
        if request.query_params.get('task'):
            qs = qs.filter(task_id=int(request.query_params['task']))
        return Response({'results': SubTaskSerializer(qs[:100], many=True).data})

    @transaction.atomic
    def post(self, request):
        task_id = int(request.data.get('task'))
        if not visible_tasks_queryset(request.user).filter(pk=task_id).exists():
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        ser = SubTaskSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        sub = ser.save(create_by=request.user.get_username(), modified_by=request.user.get_username())
        return Response(SubTaskSerializer(sub).data, status=status.HTTP_201_CREATED)


class SubTaskDetailAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['patch', 'put', 'delete', 'options']

    def patch(self, request, pk):
        sub = toggle_subtask(
            request.user, pk,
            is_completed=request.data.get('is_completed'),
            new_status=request.data.get('status'),
        )
        return Response(SubTaskSerializer(sub).data)


class TaskCommentListCreateAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        vis = visible_tasks_queryset(request.user)
        qs = TaskComment.objects.filter(task__in=vis).select_related('author')
        if request.query_params.get('task'):
            qs = qs.filter(task_id=int(request.query_params['task']))
        return Response({'results': TaskCommentSerializer(qs[:100], many=True).data})

    @transaction.atomic
    def post(self, request):
        task_id = int(request.data.get('task'))
        if not visible_tasks_queryset(request.user).filter(pk=task_id).exists():
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        ser = TaskCommentSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        comment = ser.save(author=request.user)
        return Response(TaskCommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class TaskReportAPIView(BaseAPIMixin, APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        return Response(build_task_reports(request.user))
