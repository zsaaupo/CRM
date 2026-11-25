from django.urls import path
from .views import TaskAPI, TaskPage


app_name = 'tasks'
urlpatterns = []
for prefix, view, suffix in [('', TaskPage, ''), ('api/', TaskAPI, '-api')]:
    for route, name, area, methods in [
        ('', 'list', 'list', ['get', 'post']),
        ('responsibility/', 'responsibility', 'responsibility', ['get']),
        ('reports/', 'report', 'report', ['get']),
        ('groups/', 'groups', 'groups', ['get', 'post']),
        ('<int:pk>/', 'detail', 'detail', ['get', 'post']),
        ('<int:pk>/subtasks/', 'subtask', 'subtask', ['post']),
        ('<int:pk>/subtasks/<int:child_pk>/', 'subtask-update', 'subtask', ['post']),
    ]:
        urlpatterns.append(path(prefix + route, view.as_view(area=area, http_method_names=[*methods, 'options']),
                                name=name + suffix))
