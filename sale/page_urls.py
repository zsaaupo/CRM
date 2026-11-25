from django.urls import path
from .quotation_purchase import QuotePurchasePage

from accounting.page_views import SalesPaymentListPageView

from .page_views import (
    QuotationCreatePageView, QuotationDetailPageView, QuotationEditPageView, QuotationListPageView,
    SalesCreatePageView, SalesDetailPageView, SalesEditPageView, SalesListPageView,
    SalesReturnCreatePageView, SalesReturnDetailPageView, SalesReturnEditPageView, SalesReturnListPageView,
)


app_name = 'sale_pages'

urlpatterns = [
    path('quotations/<int:pk>/purchase/', QuotePurchasePage.as_view(), name='quote-purchase'),
    path('sales-orders/<int:pk>/edit/', SalesEditPageView.as_view(), name='edit-sale'),
    path('sales-orders/', SalesListPageView.as_view(), name='sale-list'),
    path('sales-orders/add/', SalesCreatePageView.as_view(), name='create-sale'),
    path('sales-orders/<int:pk>/', SalesDetailPageView.as_view(), name='sale-detail'),
    path('sales-orders/<int:sales_order_pk>/payments/', SalesPaymentListPageView.as_view(), name='sales-payment-list'),
    path('sales-returns/', SalesReturnListPageView.as_view(), name='return-list'),
    path('sales-returns/add/', SalesReturnCreatePageView.as_view(), name='create-return'),
    path('sales-returns/<int:pk>/', SalesReturnDetailPageView.as_view(), name='return-detail'),
    path('sales-returns/<int:pk>/edit/', SalesReturnEditPageView.as_view(), name='edit-return'),
    path('quotations/', QuotationListPageView.as_view(), name='quotation-list'),
    path('quotations/add/', QuotationCreatePageView.as_view(), name='quotation-create'),
    path('quotations/<int:pk>/', QuotationDetailPageView.as_view(), name='quotation-detail'),
    path('quotations/<int:pk>/edit/', QuotationEditPageView.as_view(), name='quotation-edit'),
]
