from django.urls import path

from .page_views import AuditLogPageView


urlpatterns = [
    path('', AuditLogPageView.as_view(), name='list'),
]
