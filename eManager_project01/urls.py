from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from account.page_views import DashboardPageView

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='dashboard', permanent=False), name='home'),
    path('dashboard/', DashboardPageView.as_view(), name='dashboard'),
    path('admin/', admin.site.urls),
    path('account/api/', include('account.api_urls')),
    path('account/', include('account.page_urls')),
]
