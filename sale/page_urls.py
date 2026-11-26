from django.urls import path
from . import instrument_views
from .pos_views import POSPage

from accounting.page_views import SalesPaymentListPageView

from .page_views import (
    QuotationCreatePageView, QuotationDetailPageView, QuotationEditPageView, QuotationListPageView,
    SalesCreatePageView, SalesDetailPageView, SalesEditPageView, SalesImportPageView, SalesListPageView,
    SalesReturnCreatePageView, SalesReturnDetailPageView, SalesReturnEditPageView, SalesReturnListPageView,
)


app_name = 'sale_pages'

urlpatterns = [
    path('pos/', POSPage.as_view(), name='pos'),
    path('coupons/', instrument_views.InstrumentPage.as_view(is_card=False), name='coupons'),
    path('gift-cards/', instrument_views.InstrumentPage.as_view(), name='gift-cards'),
    path('gift-cards/<int:pk>/', instrument_views.GiftCardDetailPage.as_view(), name='gift-card-detail'),
    path('gift-cards/<int:pk>/card.png', instrument_views.card_image, name='gift-card-image'),
    path('sales-orders/<int:pk>/edit/', SalesEditPageView.as_view(), name='edit-sale'),
    path('sales-orders/', SalesListPageView.as_view(), name='sale-list'),
    path('sales-orders/add/', SalesCreatePageView.as_view(), name='create-sale'),
    path('sales-orders/import/', SalesImportPageView.as_view(), name='import-sale'),
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
