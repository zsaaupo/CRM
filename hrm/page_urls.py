from django.urls import path

from .catalog import RESOURCES
from .page_views import (
    EmployeeDetailPageView, HRMAddPageView, HRMEditPageView, HRMListPageView,
    LeaveApplyPageView, LeaveRequestDetailPageView, LeaveRequestListPageView,
)


app_name = 'hrm_pages'

urlpatterns = [
    path('employees/<int:pk>/', EmployeeDetailPageView.as_view(), name='employee-detail'),
    path('leave-requests/', LeaveRequestListPageView.as_view(), name='leave-request-list'),
    path('leave-requests/add/', LeaveApplyPageView.as_view(), name='add-leave-request'),
    path('leave-requests/<int:pk>/', LeaveRequestDetailPageView.as_view(), name='leave-request-detail'),
]
for resource in RESOURCES:
    urlpatterns += [
        path(f'{resource.path}/', HRMListPageView.as_view(resource_key=resource.key), name=f'{resource.key}-list'),
        path(f'{resource.path}/add/', HRMAddPageView.as_view(resource_key=resource.key), name=f'add-{resource.key}'),
        path(f'{resource.path}/<int:pk>/edit/', HRMEditPageView.as_view(resource_key=resource.key), name=f'edit-{resource.key}'),
    ]
