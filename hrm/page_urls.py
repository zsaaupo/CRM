from django.urls import path

from .catalog import RESOURCES
from .page_views import (
    AttendanceImportPageView, EmployeeDetailPageView, HRMAddPageView, HRMEditPageView, HRMListPageView,
    LeaveApplyPageView, LeaveBalanceAllocationPageView, LeaveBalanceListPageView,
    LeaveRequestDetailPageView, LeaveRequestListPageView, NoticeBoardPageView,
    PayrollAdjustmentDetailPageView, PayrollCalculationPageView, PayrollReportPageView, PayrollRunDetailPageView,
)
from .promotion_pages import (PromotionAddPageView, PromotionCorrectionPageView, PromotionDetailPageView,
                              PromotionListPageView)


app_name = 'hrm_pages'

urlpatterns = [
    path('notices/', NoticeBoardPageView.as_view(), name='notice-board'),
    path('promotions/', PromotionListPageView.as_view(), name='promotion-list'),
    path('promotions/add/', PromotionAddPageView.as_view(), name='add-promotion'),
    path('promotions/<int:pk>/', PromotionDetailPageView.as_view(), name='promotion-detail'),
    path('promotions/<int:pk>/correct/', PromotionCorrectionPageView.as_view(), name='correct-promotion'),
    path('attendance/import/', AttendanceImportPageView.as_view(), name='import-attendance'),
    path('employees/<int:pk>/', EmployeeDetailPageView.as_view(), name='employee-detail'),
    path('leave-requests/', LeaveRequestListPageView.as_view(), name='leave-request-list'),
    path('leave-requests/add/', LeaveApplyPageView.as_view(), name='add-leave-request'),
    path('leave-requests/<int:pk>/', LeaveRequestDetailPageView.as_view(), name='leave-request-detail'),
    path('leave-balances/', LeaveBalanceListPageView.as_view(), name='leave-balance-list'),
    path('leave-balances/allocate/', LeaveBalanceAllocationPageView.as_view(), name='allocate-leave-balances'),
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
