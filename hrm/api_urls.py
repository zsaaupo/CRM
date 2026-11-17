from django.urls import path

from .api_views import (
    DepartmentDetailAPIView, DepartmentListAPIView, DepartmentListCreateAPIView, DepartmentUpdateAPIView,
    DesignationDetailAPIView, DesignationListAPIView, DesignationListCreateAPIView, DesignationUpdateAPIView,
    EmployeeDetailAPIView, EmployeeEditOptionsAPIView, EmployeeListAPIView, EmployeeListCreateAPIView,
    EmployeeOptionsAPIView, EmployeeUpdateAPIView,
    EmployeeStatusDetailAPIView, EmployeeStatusListAPIView, EmployeeStatusListCreateAPIView, EmployeeStatusUpdateAPIView,
    EmploymentTypeDetailAPIView, EmploymentTypeListAPIView, EmploymentTypeListCreateAPIView, EmploymentTypeUpdateAPIView,
)


app_name = 'hrm'

urlpatterns = [
    path('departments/', DepartmentListCreateAPIView.as_view(), name='department-list'),
    path('departments/list/', DepartmentListAPIView.as_view(), name='department-list-view'),
    path('departments/<int:pk>/', DepartmentDetailAPIView.as_view(), name='department-detail'),
    path('departments/<int:pk>/update/', DepartmentUpdateAPIView.as_view(), name='department-update'),
    path('designations/', DesignationListCreateAPIView.as_view(), name='designation-list'),
    path('designations/list/', DesignationListAPIView.as_view(), name='designation-list-view'),
    path('designations/<int:pk>/', DesignationDetailAPIView.as_view(), name='designation-detail'),
    path('designations/<int:pk>/update/', DesignationUpdateAPIView.as_view(), name='designation-update'),
    path('employment-types/', EmploymentTypeListCreateAPIView.as_view(), name='employment-type-list'),
    path('employment-types/list/', EmploymentTypeListAPIView.as_view(), name='employment-type-list-view'),
    path('employment-types/<int:pk>/', EmploymentTypeDetailAPIView.as_view(), name='employment-type-detail'),
    path('employment-types/<int:pk>/update/', EmploymentTypeUpdateAPIView.as_view(), name='employment-type-update'),
    path('employee-statuses/', EmployeeStatusListCreateAPIView.as_view(), name='employee-status-list'),
    path('employee-statuses/list/', EmployeeStatusListAPIView.as_view(), name='employee-status-list-view'),
    path('employee-statuses/<int:pk>/', EmployeeStatusDetailAPIView.as_view(), name='employee-status-detail'),
    path('employee-statuses/<int:pk>/update/', EmployeeStatusUpdateAPIView.as_view(), name='employee-status-update'),
    path('employees/', EmployeeListCreateAPIView.as_view(), name='employee-list'),
    path('employees/list/', EmployeeListAPIView.as_view(), name='employee-list-view'),
    path('employees/options/', EmployeeOptionsAPIView.as_view(), name='employee-options'),
    path('employees/<int:pk>/options/', EmployeeEditOptionsAPIView.as_view(), name='employee-edit-options'),
    path('employees/<int:pk>/', EmployeeDetailAPIView.as_view(), name='employee-detail'),
    path('employees/<int:pk>/update/', EmployeeUpdateAPIView.as_view(), name='employee-update'),
]
