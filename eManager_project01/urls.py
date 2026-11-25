from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from account.page_views import DashboardPageView
from sale.page_views import PosTerminalPageView

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='dashboard', permanent=False), name='home'),
    path('dashboard/', DashboardPageView.as_view(), name='dashboard'),
    path('pos/', PosTerminalPageView.as_view(), name='pos'),
    path('admin/', admin.site.urls),
    path('account/api/', include('account.api_urls')),
    path('inventory/api/', include('inventory.api_urls')),
    path('warehouse/api/', include('warehouse.api_urls')),
    path('purchase/api/', include('purchase.api_urls')),
    path('sale/api/', include('sale.api_urls')),
    path('people/api/', include('people.api_urls')),
    path('hrm/api/', include('hrm.api_urls')),
    path('accounting/api/', include('accounting.api_urls')),
    path('manufacturing/api/', include('manufacturing.api_urls')),
    path('tasks/api/', include('tasks.api_urls')),
    path('common/api/', include('common.api_urls')),
    path('report/api/', include('report.urls')),
    path('inventory/', include('inventory.page_urls')),
    path('warehouse/', include('warehouse.page_urls')),
    path('purchase/', include('purchase.page_urls')),
    path('sale/', include('sale.page_urls')),
    path('people/', include('people.page_urls')),
    path('hrm/', include('hrm.page_urls')),
    path('accounting/', include('accounting.page_urls')),
    path('manufacturing/', include('manufacturing.page_urls')),
    path('tasks/', include('tasks.page_urls')),
    path('settings/api/', include('common.ops_api_urls')),
    path('settings/', include('common.page_urls')),
    path('report/', include('report.page_urls')),
    path('account/', include('account.page_urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
