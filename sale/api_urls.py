from django.urls import path

from .api_views import (
    SalesOrderDetailAPIView, SalesOrderItemDetailAPIView, SalesOrderItemListAPIView,
    SalesOrderItemListCreateAPIView, SalesOrderItemUpdateAPIView, SalesOrderListAPIView,
    SalesEditOptionsAPIView, SalesOptionsAPIView, SalesOrderListCreateAPIView, SalesOrderUpdateAPIView,
)


app_name = 'sale'

urlpatterns = [
    path('sales-orders/edit-options/', SalesEditOptionsAPIView.as_view(), name='sales-edit-options'),
    path('sales-orders/options/', SalesOptionsAPIView.as_view(), name='sale-options'),
    path('sales-orders/', SalesOrderListCreateAPIView.as_view(), name='sales-order-list'),
    path('sales-orders/list/', SalesOrderListAPIView.as_view(), name='sales-order-list-view'),
    path('sales-orders/<int:pk>/', SalesOrderDetailAPIView.as_view(), name='sales-order-detail'),
    path('sales-orders/<int:pk>/update/', SalesOrderUpdateAPIView.as_view(), name='sales-order-update'),
    path('sales-order-items/', SalesOrderItemListCreateAPIView.as_view(), name='sales-order-item-list'),
    path('sales-order-items/list/', SalesOrderItemListAPIView.as_view(), name='sales-order-item-list-view'),
    path('sales-order-items/<int:pk>/', SalesOrderItemDetailAPIView.as_view(), name='sales-order-item-detail'),
    path('sales-order-items/<int:pk>/update/', SalesOrderItemUpdateAPIView.as_view(), name='sales-order-item-update'),
]
