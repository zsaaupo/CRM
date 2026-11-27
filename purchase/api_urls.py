from django.urls import path
from .receipt_views import ReceiptAPI
from common.order_copy import DuplicateOrderView
from purchase.models import PurchaseOrder

from .api_views import (
    PurchaseOrderDetailAPIView, PurchaseOrderItemDetailAPIView, PurchaseOrderItemListAPIView,
    PurchaseOrderItemListCreateAPIView, PurchaseOrderItemUpdateAPIView, PurchaseOrderListAPIView,
    PurchaseOptionsAPIView, PurchaseEditOptionsAPIView, PurchaseOrderListCreateAPIView, PurchaseOrderUpdateAPIView, PurchaseReceiveAPIView,
)
from .return_api import (
    PurchaseReturnCompleteAPIView, PurchaseReturnDetailAPIView, PurchaseReturnListAPIView,
    PurchaseReturnListCreateAPIView, PurchaseReturnOptionsAPIView, PurchaseReturnUpdateAPIView,
)


app_name = 'purchase'

urlpatterns = [
    path('purchase-orders/<int:pk>/receipts/', ReceiptAPI.as_view(), name='receipts'),
    path('purchase-orders/<int:pk>/receipt-invoices/', ReceiptAPI.as_view(invoice=True, http_method_names=['post', 'options']), name='receipt-invoice'),
    path('purchase-orders/<int:pk>/duplicate/', DuplicateOrderView.as_view(model=PurchaseOrder), name='duplicate-order'),
    path('purchase-orders/options/', PurchaseOptionsAPIView.as_view(), name='purchase-options'),
    path('purchase-orders/edit-options/', PurchaseEditOptionsAPIView.as_view(), name='purchase-edit-options'),
    path('purchase-orders/receive/', PurchaseReceiveAPIView.as_view(), name='purchase-order-receive'),
    path('purchase-orders/', PurchaseOrderListCreateAPIView.as_view(), name='purchase-order-list'),
    path('purchase-orders/list/', PurchaseOrderListAPIView.as_view(), name='purchase-order-list-view'),
    path('purchase-orders/<int:pk>/', PurchaseOrderDetailAPIView.as_view(), name='purchase-order-detail'),
    path('purchase-orders/<int:pk>/update/', PurchaseOrderUpdateAPIView.as_view(), name='purchase-order-update'),
    path('purchase-order-items/', PurchaseOrderItemListCreateAPIView.as_view(), name='purchase-order-item-list'),
    path('purchase-order-items/list/', PurchaseOrderItemListAPIView.as_view(), name='purchase-order-item-list-view'),
    path('purchase-order-items/<int:pk>/', PurchaseOrderItemDetailAPIView.as_view(), name='purchase-order-item-detail'),
    path('purchase-order-items/<int:pk>/update/', PurchaseOrderItemUpdateAPIView.as_view(), name='purchase-order-item-update'),
    path('purchase-returns/', PurchaseReturnListCreateAPIView.as_view(), name='purchase-return-list'),
    path('purchase-returns/list/', PurchaseReturnListAPIView.as_view(), name='purchase-return-list-view'),
    path('purchase-returns/options/', PurchaseReturnOptionsAPIView.as_view(), name='purchase-return-options'),
    path('purchase-returns/complete/', PurchaseReturnCompleteAPIView.as_view(), name='purchase-return-complete'),
    path('purchase-returns/<int:pk>/', PurchaseReturnDetailAPIView.as_view(), name='purchase-return-detail'),
    path('purchase-returns/<int:pk>/update/', PurchaseReturnUpdateAPIView.as_view(), name='purchase-return-update'),
    path('purchase-returns/<int:pk>/options/', PurchaseReturnOptionsAPIView.as_view(), name='purchase-return-edit-options'),
]
