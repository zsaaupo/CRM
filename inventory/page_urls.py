from django.urls import path

from .catalog import RESOURCES
from .page_views import InventoryAddPageView, InventoryListPageView


app_name = 'inventory_pages'

urlpatterns = []
for resource in RESOURCES:
    urlpatterns += [
        path(f'{resource.path}/', InventoryListPageView.as_view(resource_key=resource.key), name=f'{resource.key}-list'),
        path(f'{resource.path}/add/', InventoryAddPageView.as_view(resource_key=resource.key), name=f'add-{resource.key}'),
    ]
