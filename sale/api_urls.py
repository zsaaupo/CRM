from django.urls import path

from .api_views import (
    SalesOrderDetailAPIView, SalesOrderItemDetailAPIView, SalesOrderItemListAPIView,
    SalesOrderItemListCreateAPIView, SalesOrderItemUpdateAPIView, SalesOrderListAPIView,
    SalesEditOptionsAPIView, SalesOptionsAPIView, SalesOrderListCreateAPIView, SalesOrderUpdateAPIView, SaleCompleteAPIView,
)
from .return_api import (SalesReturnCompleteAPIView, SalesReturnDetailAPIView, SalesReturnListAPIView,
                         SalesReturnListCreateAPIView, SalesReturnOptionsAPIView, SalesReturnUpdateAPIView)


app_name = 'sale'

urlpatterns = [
    path('sales-orders/complete/', SaleCompleteAPIView.as_view(), name='sales-order-complete'),
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
    path('sales-returns/', SalesReturnListCreateAPIView.as_view(), name='sales-return-list'),
    path('sales-returns/list/', SalesReturnListAPIView.as_view(), name='sales-return-list-view'),
    path('sales-returns/options/', SalesReturnOptionsAPIView.as_view(), name='sales-return-options'),
    path('sales-returns/complete/', SalesReturnCompleteAPIView.as_view(), name='sales-return-complete'),
    path('sales-returns/<int:pk>/', SalesReturnDetailAPIView.as_view(), name='sales-return-detail'),
    path('sales-returns/<int:pk>/update/', SalesReturnUpdateAPIView.as_view(), name='sales-return-update'),
    path('sales-returns/<int:pk>/options/', SalesReturnOptionsAPIView.as_view(), name='sales-return-edit-options'),
]
