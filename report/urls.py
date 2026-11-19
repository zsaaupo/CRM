from django.urls import path

from hrm.payroll_reports import PayslipPDFAPIView
from .views import PurchasePDFAPIView, SalePDFAPIView, TransferPDFAPIView


app_name = 'report'
urlpatterns = [
    path('purchases/<int:pk>/pdf/', PurchasePDFAPIView.as_view(), name='purchase-pdf'),
    path('sales/<int:pk>/pdf/', SalePDFAPIView.as_view(), name='sale-pdf'),
    path('transfers/<int:pk>/pdf/', TransferPDFAPIView.as_view(), name='transfer-pdf'),
    path('payslips/<int:pk>/pdf/', PayslipPDFAPIView.as_view(), name='payslip-pdf'),
]
