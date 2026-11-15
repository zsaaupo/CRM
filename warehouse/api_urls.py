from django.urls import path

from .api_views import (
    StockDetailAPIView, StockListAPIView,
    RoleWarehouseAccessDetailAPIView, RoleWarehouseAccessListAPIView,
    RoleWarehouseAccessListCreateAPIView, RoleWarehouseAccessUpdateAPIView,
    WareHouseDetailAPIView, WareHouseListAPIView, WareHouseListCreateAPIView,
    WareHouseUpdateAPIView,
)


app_name = 'warehouse'

urlpatterns = [
    path('role-warehouse-access/', RoleWarehouseAccessListCreateAPIView.as_view(), name='role-warehouse-access-list'),
    path('role-warehouse-access/list/', RoleWarehouseAccessListAPIView.as_view(), name='role-warehouse-access-list-view'),
    path('role-warehouse-access/<int:pk>/', RoleWarehouseAccessDetailAPIView.as_view(), name='role-warehouse-access-detail'),
    path('role-warehouse-access/<int:pk>/update/', RoleWarehouseAccessUpdateAPIView.as_view(), name='role-warehouse-access-update'),
    path('stocks/list/', StockListAPIView.as_view(), name='stock-list'),
    path('stocks/<int:pk>/', StockDetailAPIView.as_view(), name='stock-detail'),
    path('warehouses/', WareHouseListCreateAPIView.as_view(), name='warehouse-list'),
    path('warehouses/list/', WareHouseListAPIView.as_view(), name='warehouse-list-view'),
    path('warehouses/<int:pk>/', WareHouseDetailAPIView.as_view(), name='warehouse-detail'),
    path('warehouses/<int:pk>/update/', WareHouseUpdateAPIView.as_view(), name='warehouse-update'),
]
