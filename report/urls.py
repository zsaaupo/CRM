from django.urls import path

from .views import (
    AnalyticsReportAPIView, BarcodePDFAPIView, DashboardAPIView,
    PosReceiptPDFAPIView, ShippingDocumentPDFAPIView,
)

from hrm.payroll_reports import PayslipPDFAPIView
from .views import PurchasePDFAPIView, QuotationPDFAPIView, SalePDFAPIView, TransferPDFAPIView


app_name = 'report'
urlpatterns = [
    path('dashboard/', DashboardAPIView.as_view(), name='dashboard-api'),
    path('analytics/<slug:slug>/', AnalyticsReportAPIView.as_view(), name='analytics-api'),
    path('barcodes/pdf/', BarcodePDFAPIView.as_view(), name='barcodes-pdf'),
    path('pos/<int:pk>/receipt/', PosReceiptPDFAPIView.as_view(), name='pos-receipt-pdf'),
    path('shipping/<str:kind>/<int:pk>/pdf/', ShippingDocumentPDFAPIView.as_view(), name='shipping-pdf'),
    path('purchases/<int:pk>/pdf/', PurchasePDFAPIView.as_view(), name='purchase-pdf'),
    path('sales/<int:pk>/pdf/', SalePDFAPIView.as_view(), name='sale-pdf'),
    path('quotations/<int:pk>/pdf/', QuotationPDFAPIView.as_view(), name='quotation-pdf'),
    path('transfers/<int:pk>/pdf/', TransferPDFAPIView.as_view(), name='transfer-pdf'),
    path('payslips/<int:pk>/pdf/', PayslipPDFAPIView.as_view(), name='payslip-pdf'),
]
