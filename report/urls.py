from django.urls import path

from .views import PurchasePDFAPIView, SalePDFAPIView, TransferPDFAPIView


app_name = 'report'
urlpatterns = [
    path('purchases/<int:pk>/pdf/', PurchasePDFAPIView.as_view(), name='purchase-pdf'),
    path('sales/<int:pk>/pdf/', SalePDFAPIView.as_view(), name='sale-pdf'),
    path('transfers/<int:pk>/pdf/', TransferPDFAPIView.as_view(), name='transfer-pdf'),
]
