from django.urls import path

from .api_views import (
    SubTaskDetailAPIView, SubTaskListCreateAPIView, TaskCommentListCreateAPIView,
    TaskDetailAPIView, TaskGroupListCreateAPIView, TaskListCreateAPIView,
    TaskReportAPIView, TaskSeenAPIView,
)


app_name = 'tasks'

urlpatterns = [
    path('tasks/', TaskListCreateAPIView.as_view(), name='task-list'),
    path('tasks/<int:pk>/', TaskDetailAPIView.as_view(), name='task-detail'),
    path('tasks/<int:pk>/seen/', TaskSeenAPIView.as_view(), name='task-seen'),
    path('groups/', TaskGroupListCreateAPIView.as_view(), name='group-list'),
    path('subtasks/', SubTaskListCreateAPIView.as_view(), name='subtask-list'),
    path('subtasks/<int:pk>/', SubTaskDetailAPIView.as_view(), name='subtask-detail'),
    path('comments/', TaskCommentListCreateAPIView.as_view(), name='comment-list'),
    path('reports/', TaskReportAPIView.as_view(), name='report'),
]
