"""Account JSON API routes."""

from django.urls import path

from .api_views import (
    AddUserAPIView,
    AssignGroupAPIView,
    GroupDetailAPIView,
    GroupListAPIView,
    GroupPermissionsAPIView,
    LoginAPIView,
    LogoutAPIView,
    PermissionListAPIView,
    ResendOTPAPIView,
    SignUpAPIView,
    UserListAPIView,
    UserGroupsAPIView,
    VerifyOTPAPIView,
)


app_name = 'account'

urlpatterns = [
    path('login/', LoginAPIView.as_view(), name='login'),
    path('logout/', LogoutAPIView.as_view(), name='logout'),
    path('add-user/', AddUserAPIView.as_view(), name='add-user'),
    path('users/', UserListAPIView.as_view(), name='user-list'),
    path('users/<int:pk>/groups/', UserGroupsAPIView.as_view(), name='user-groups'),
    path('assign-group/', AssignGroupAPIView.as_view(), name='assign-group'),
    path('permissions/', PermissionListAPIView.as_view(), name='permission-list'),
    path('groups/', GroupListAPIView.as_view(), name='group-list'),
    path('groups/<int:pk>/', GroupDetailAPIView.as_view(), name='group-detail'),
    path('groups/<int:pk>/permissions/', GroupPermissionsAPIView.as_view(), name='group-permissions'),
    path('signup/', SignUpAPIView.as_view(), name='signup'),
    path('verify-otp/', VerifyOTPAPIView.as_view(), name='verify-otp'),
    path('resend-otp/', ResendOTPAPIView.as_view(), name='resend-otp'),
]
