from django.urls import path

from .catalog import RESOURCES
from .page_views import EmployeeDetailPageView, HRMAddPageView, HRMEditPageView, HRMListPageView


app_name = 'hrm_pages'

urlpatterns = [
    path('employees/<int:pk>/', EmployeeDetailPageView.as_view(), name='employee-detail'),
]
for resource in RESOURCES:
    urlpatterns += [
        path(f'{resource.path}/', HRMListPageView.as_view(resource_key=resource.key), name=f'{resource.key}-list'),
        path(f'{resource.path}/add/', HRMAddPageView.as_view(resource_key=resource.key), name=f'add-{resource.key}'),
        path(f'{resource.path}/<int:pk>/edit/', HRMEditPageView.as_view(resource_key=resource.key), name=f'edit-{resource.key}'),
    ]
