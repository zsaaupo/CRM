from django.urls import path

from .page_views import StockAddPageView, WareHouseAddPageView, WareHouseEditPageView, WareHouseListPageView


app_name = 'warehouse_pages'

urlpatterns = [
    path('stocks/add/', StockAddPageView.as_view(), name='add-stock'),
    path('warehouses/', WareHouseListPageView.as_view(), name='warehouse-list'),
    path('warehouses/add/', WareHouseAddPageView.as_view(), name='add-warehouse'),
    path('warehouses/<int:pk>/edit/', WareHouseEditPageView.as_view(), name='edit-warehouse'),
]
