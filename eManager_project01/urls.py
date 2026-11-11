from django.conf import settings
from django.conf.urls.static import static
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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
