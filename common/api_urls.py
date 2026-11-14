from django.urls import path

from .api_views import (
    TradeStatusDetailAPIView, TradeStatusListAPIView, TradeStatusListCreateAPIView, TradeStatusUpdateAPIView,
)


app_name = 'common'

urlpatterns = [
    path('trade-statuses/', TradeStatusListCreateAPIView.as_view(), name='trade-status-list'),
    path('trade-statuses/list/', TradeStatusListAPIView.as_view(), name='trade-status-list-view'),
    path('trade-statuses/<int:pk>/', TradeStatusDetailAPIView.as_view(), name='trade-status-detail'),
    path('trade-statuses/<int:pk>/update/', TradeStatusUpdateAPIView.as_view(), name='trade-status-update'),
]
