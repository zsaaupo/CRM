from django.urls import path

from .catalog import RESOURCES
from .page_views import (
    EmployeeDetailPageView, HRMAddPageView, HRMEditPageView, HRMListPageView,
    LeaveApplyPageView, LeaveRequestDetailPageView, LeaveRequestListPageView,
    PayrollAdjustmentDetailPageView, PayrollCalculationPageView, PayrollReportPageView, PayrollRunDetailPageView,
    WorkforceConsolePageView,
)


app_name = 'hrm_pages'

urlpatterns = [
    path('workforce/', WorkforceConsolePageView.as_view(), name='workforce-console'),
    path('employees/<int:pk>/', EmployeeDetailPageView.as_view(), name='employee-detail'),
    path('leave-requests/', LeaveRequestListPageView.as_view(), name='leave-request-list'),
    path('leave-requests/add/', LeaveApplyPageView.as_view(), name='add-leave-request'),
    path('leave-requests/<int:pk>/', LeaveRequestDetailPageView.as_view(), name='leave-request-detail'),
    path('payroll-runs/', HRMListPageView.as_view(resource_key='payroll-run'), name='payroll-run-list'),
    path('payroll-runs/add/', PayrollCalculationPageView.as_view(), name='add-payroll-run'),
    path('payroll-runs/<int:pk>/', PayrollRunDetailPageView.as_view(), name='payroll-run-detail'),
    path('payroll-adjustments/<int:pk>/', PayrollAdjustmentDetailPageView.as_view(), name='payroll-adjustment-detail'),
    path('payroll-reports/', PayrollReportPageView.as_view(), name='payroll-report'),
]
for resource in RESOURCES:
    urlpatterns += [
        path(f'{resource.path}/', HRMListPageView.as_view(resource_key=resource.key), name=f'{resource.key}-list'),
        path(f'{resource.path}/add/', HRMAddPageView.as_view(resource_key=resource.key), name=f'add-{resource.key}'),
        path(f'{resource.path}/<int:pk>/edit/', HRMEditPageView.as_view(resource_key=resource.key), name=f'edit-{resource.key}'),
    ]
