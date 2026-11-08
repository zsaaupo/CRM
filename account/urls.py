from django.urls import path
from .views import ResendOTPAPIView, SignUpAPIView, VerifyOTPAPIView


app_name = 'account'

urlpatterns = [
    path('signup/', SignUpAPIView.as_view(), name='signup'),
    path('verify-otp/', VerifyOTPAPIView.as_view(), name='verify-otp'),
    path('resend-otp/', ResendOTPAPIView.as_view(), name='resend-otp'),
]
