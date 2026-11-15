from django.urls import path

from .page_views import PurchaseCreatePageView, PurchaseDetailPageView, PurchaseEditPageView, PurchaseListPageView


app_name = 'purchase_pages'

urlpatterns = [
    path('purchase-orders/', PurchaseListPageView.as_view(), name='purchase-list'),
    path('purchase-orders/add/', PurchaseCreatePageView.as_view(), name='create-purchase'),
    path('purchase-orders/<int:pk>/', PurchaseDetailPageView.as_view(), name='purchase-detail'),
    path('purchase-orders/<int:pk>/edit/', PurchaseEditPageView.as_view(), name='edit-purchase'),
]
