from django.urls import path
from .page_views import ManufacturingHomePageView

app_name = 'manufacturing_pages'

urlpatterns = [
    path('', ManufacturingHomePageView.as_view(), name='home'),
]
