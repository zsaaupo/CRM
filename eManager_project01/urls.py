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
    path('inventory/api/', include('inventory.api_urls')),
    path('warehouse/api/', include('warehouse.api_urls')),
    path('purchase/api/', include('purchase.api_urls')),
    path('sale/api/', include('sale.api_urls')),
    path('people/api/', include('people.api_urls')),
    path('inventory/', include('inventory.page_urls')),
    path('warehouse/', include('warehouse.page_urls')),
    path('purchase/', include('purchase.page_urls')),
    path('sale/', include('sale.page_urls')),
    path('people/', include('people.page_urls')),
    path('account/', include('account.page_urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
