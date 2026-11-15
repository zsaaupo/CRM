from django.urls import path

from .api_views import (
    StockCreateAPIView, StockDetailAPIView, StockListAPIView, StockOptionsAPIView,
    WareHouseDetailAPIView, WareHouseListAPIView, WareHouseListCreateAPIView,
    WareHouseUpdateAPIView,
)


app_name = 'warehouse'

urlpatterns = [
    path('stocks/', StockCreateAPIView.as_view(), name='stock-add'),
    path('stocks/options/', StockOptionsAPIView.as_view(), name='stock-options'),
    path('stocks/list/', StockListAPIView.as_view(), name='stock-list'),
    path('stocks/<int:pk>/', StockDetailAPIView.as_view(), name='stock-detail'),
    path('warehouses/', WareHouseListCreateAPIView.as_view(), name='warehouse-list'),
    path('warehouses/list/', WareHouseListAPIView.as_view(), name='warehouse-list-view'),
    path('warehouses/<int:pk>/', WareHouseDetailAPIView.as_view(), name='warehouse-detail'),
    path('warehouses/<int:pk>/update/', WareHouseUpdateAPIView.as_view(), name='warehouse-update'),
]
