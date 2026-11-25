from django.urls import include, path


app_name = 'audit_log'
urlpatterns = [
    path('api/', include('audit_log.api_urls')),
    path('', include('audit_log.page_urls')),
]
