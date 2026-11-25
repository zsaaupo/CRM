from django.urls import path

from .api_view import AuditLogListAPIView


urlpatterns = [
    path('', AuditLogListAPIView.as_view(), name='api-list'),
]
