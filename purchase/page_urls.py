from django.urls import path
from .receipt_views import ReceiptPage

from .page_views import (
    PurchaseCreatePageView, PurchaseDetailPageView, PurchaseEditPageView, PurchaseListPageView,
    PurchaseReturnCreatePageView, PurchaseReturnDetailPageView, PurchaseReturnEditPageView, PurchaseReturnListPageView,
)


app_name = 'purchase_pages'

urlpatterns = [
    path('purchase-orders/<int:pk>/receipts/', ReceiptPage.as_view(), name='receipts'),
    path('purchase-orders/<int:pk>/receipt-invoices/', ReceiptPage.as_view(invoice=True, http_method_names=['post', 'options']), name='receipt-invoice'),
    path('purchase-orders/', PurchaseListPageView.as_view(), name='purchase-list'),
    path('purchase-orders/add/', PurchaseCreatePageView.as_view(), name='create-purchase'),
    path('purchase-orders/<int:pk>/', PurchaseDetailPageView.as_view(), name='purchase-detail'),
    path('purchase-orders/<int:pk>/edit/', PurchaseEditPageView.as_view(), name='edit-purchase'),
    path('purchase-returns/', PurchaseReturnListPageView.as_view(), name='return-list'),
    path('purchase-returns/add/', PurchaseReturnCreatePageView.as_view(), name='create-return'),
    path('purchase-returns/<int:pk>/', PurchaseReturnDetailPageView.as_view(), name='return-detail'),
    path('purchase-returns/<int:pk>/edit/', PurchaseReturnEditPageView.as_view(), name='edit-return'),
]
