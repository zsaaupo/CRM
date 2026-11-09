"""Account JSON API routes."""

from django.urls import path

from .api_views import (
    AddUserAPIView,
    LoginAPIView,
    LogoutAPIView,
    ResendOTPAPIView,
    SignUpAPIView,
    UserListAPIView,
    VerifyOTPAPIView,
)


app_name = 'account'

urlpatterns = [
    path('login/', LoginAPIView.as_view(), name='login'),
    path('logout/', LogoutAPIView.as_view(), name='logout'),
    path('add-user/', AddUserAPIView.as_view(), name='add-user'),
    path('users/', UserListAPIView.as_view(), name='user-list'),
    path('signup/', SignUpAPIView.as_view(), name='signup'),
    path('verify-otp/', VerifyOTPAPIView.as_view(), name='verify-otp'),
    path('resend-otp/', ResendOTPAPIView.as_view(), name='resend-otp'),
]
