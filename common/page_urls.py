from django.urls import path

from .page_views import TradeStatusAddPageView, TradeStatusEditPageView, TradeStatusListPageView


app_name = 'common_pages'

urlpatterns = [
    path('trade-statuses/', TradeStatusListPageView.as_view(), name='trade-status-list'),
    path('trade-statuses/add/', TradeStatusAddPageView.as_view(), name='add-trade-status'),
    path('trade-statuses/<int:pk>/edit/', TradeStatusEditPageView.as_view(), name='edit-trade-status'),
]
