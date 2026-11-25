from django.urls import path

from .page_views import (
    TaskDetailPageView, TaskGroupsPageView, TaskListPageView, TaskReportsPageView,
)


app_name = 'tasks_pages'

urlpatterns = [
    path('', TaskListPageView.as_view(view_mode='all'), name='list'),
    path('responsibility/', TaskListPageView.as_view(view_mode='responsibility'), name='responsibility'),
    path('groups/', TaskGroupsPageView.as_view(), name='groups'),
    path('reports/', TaskReportsPageView.as_view(), name='reports'),
    path('<int:pk>/', TaskDetailPageView.as_view(), name='detail'),
]
