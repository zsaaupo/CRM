from django.urls import path

from .page_views import (
    AddUserPageView,
    LoginPageView,
    ProfilePageView,
    SignUpPageView,
    UserListPageView,
    VerifyOTPPageView,
)


app_name = 'account_pages'

urlpatterns = [
    path('login/', LoginPageView.as_view(), name='login'),
    path('profile/', ProfilePageView.as_view(), name='profile'),
    path('add-user/', AddUserPageView.as_view(), name='add-user'),
    path('users/', UserListPageView.as_view(), name='user-list'),
    path('signup/', SignUpPageView.as_view(), name='signup'),
    path('verify-otp/', VerifyOTPPageView.as_view(), name='verify-otp'),
]
