from django.urls import path

from .page_views import AnalyticsReportPageView


app_name = 'report_pages'

urlpatterns = [
    path('<slug:slug>/', AnalyticsReportPageView.as_view(), name='analytics'),
]
