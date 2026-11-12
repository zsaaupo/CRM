from django.urls import path

from .catalog import RESOURCES
from .page_views import InventoryAddPageView, InventoryEditPageView, InventoryListPageView


app_name = 'inventory_pages'

urlpatterns = []
for resource in RESOURCES:
    urlpatterns += [
        path(f'{resource.path}/', InventoryListPageView.as_view(resource_key=resource.key), name=f'{resource.key}-list'),
        path(f'{resource.path}/add/', InventoryAddPageView.as_view(resource_key=resource.key), name=f'add-{resource.key}'),
        path(f'{resource.path}/<int:pk>/edit/', InventoryEditPageView.as_view(resource_key=resource.key), name=f'edit-{resource.key}'),
    ]
