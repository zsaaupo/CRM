from django.urls import path

from .api_views import (
    ImportCommitAPIView, ImportJobDetailAPIView, ImportJobListAPIView, ImportPreviewAPIView, ImportTemplateAPIView,
    TradeStatusDetailAPIView, TradeStatusListAPIView, TradeStatusListCreateAPIView, TradeStatusUpdateAPIView,
)


app_name = 'common'

urlpatterns = [
    path('trade-statuses/', TradeStatusListCreateAPIView.as_view(), name='trade-status-list'),
    path('trade-statuses/list/', TradeStatusListAPIView.as_view(), name='trade-status-list-view'),
    path('trade-statuses/<int:pk>/', TradeStatusDetailAPIView.as_view(), name='trade-status-detail'),
    path('trade-statuses/<int:pk>/update/', TradeStatusUpdateAPIView.as_view(), name='trade-status-update'),
    path('imports/', ImportJobListAPIView.as_view(), name='import-job-list'),
    path('imports/<int:pk>/', ImportJobDetailAPIView.as_view(), name='import-job-detail'),
    path('imports/<int:pk>/commit/', ImportCommitAPIView.as_view(), name='import-commit'),
    path('imports/<str:kind>/template/', ImportTemplateAPIView.as_view(), name='import-template'),
    path('imports/<str:kind>/preview/', ImportPreviewAPIView.as_view(), name='import-preview'),
]
