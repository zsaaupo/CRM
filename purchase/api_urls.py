from django.urls import path

from .api_views import (
    PurchaseOrderDetailAPIView, PurchaseOrderItemDetailAPIView, PurchaseOrderItemListAPIView,
    PurchaseOrderItemListCreateAPIView, PurchaseOrderItemUpdateAPIView, PurchaseOrderListAPIView,
    PurchaseOptionsAPIView, PurchaseOrderListCreateAPIView, PurchaseOrderUpdateAPIView,
)


app_name = 'purchase'

urlpatterns = [
    path('purchase-orders/options/', PurchaseOptionsAPIView.as_view(), name='purchase-options'),
    path('purchase-orders/', PurchaseOrderListCreateAPIView.as_view(), name='purchase-order-list'),
    path('purchase-orders/list/', PurchaseOrderListAPIView.as_view(), name='purchase-order-list-view'),
    path('purchase-orders/<int:pk>/', PurchaseOrderDetailAPIView.as_view(), name='purchase-order-detail'),
    path('purchase-orders/<int:pk>/update/', PurchaseOrderUpdateAPIView.as_view(), name='purchase-order-update'),
    path('purchase-order-items/', PurchaseOrderItemListCreateAPIView.as_view(), name='purchase-order-item-list'),
    path('purchase-order-items/list/', PurchaseOrderItemListAPIView.as_view(), name='purchase-order-item-list-view'),
    path('purchase-order-items/<int:pk>/', PurchaseOrderItemDetailAPIView.as_view(), name='purchase-order-item-detail'),
    path('purchase-order-items/<int:pk>/update/', PurchaseOrderItemUpdateAPIView.as_view(), name='purchase-order-item-update'),
]
