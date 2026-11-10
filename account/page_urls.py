from django.urls import path

from .page_views import (
    AddRolePageView,
    AddUserPageView,
    AssignRolePageView,
    LoginPageView,
    ProfilePageView,
    RoleListPageView,
    RolePermissionsPageView,
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
    path('users/<int:pk>/assign-role/', AssignRolePageView.as_view(), name='assign-role'),
    path('roles/', RoleListPageView.as_view(), name='role-list'),
    path('roles/add/', AddRolePageView.as_view(), name='add-role'),
    path('roles/<int:pk>/permissions/', RolePermissionsPageView.as_view(), name='role-permissions'),
    path('signup/', SignUpPageView.as_view(), name='signup'),
    path('verify-otp/', VerifyOTPPageView.as_view(), name='verify-otp'),
]
