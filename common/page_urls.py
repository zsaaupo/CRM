from django.urls import path

from .page_views import (
    AuditLogPageView, GeneralSettingPageView, TradeStatusAddPageView,
    TradeStatusEditPageView, TradeStatusListPageView,
)


app_name = 'common_pages'

urlpatterns = [
    path('general/', GeneralSettingPageView.as_view(), name='general-settings'),
    path('audit-logs/', AuditLogPageView.as_view(), name='audit-logs'),
    path('trade-statuses/', TradeStatusListPageView.as_view(), name='trade-status-list'),
    path('trade-statuses/add/', TradeStatusAddPageView.as_view(), name='add-trade-status'),
    path('trade-statuses/<int:pk>/edit/', TradeStatusEditPageView.as_view(), name='edit-trade-status'),
]
