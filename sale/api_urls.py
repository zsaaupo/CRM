from django.urls import path
from . import instrument_views
from . import pos_views

from .api_views import (
    QuotationActionAPIView, QuotationDetailAPIView, QuotationListCreateAPIView, QuotationOptionsAPIView,
    SalesOrderDetailAPIView, SalesOrderImportAPIView, SalesOrderItemDetailAPIView, SalesOrderItemListAPIView,
    SalesOrderItemListCreateAPIView, SalesOrderItemUpdateAPIView, SalesOrderListAPIView,
    SalesEditOptionsAPIView, SalesImportOptionsAPIView, SalesOptionsAPIView, SalesOrderListCreateAPIView,
    SalesOrderUpdateAPIView, SaleCompleteAPIView,
)
from .chalan_api import (ChalanDetailAPIView, ChalanListAPIView, ChalanListCreateAPIView,
                         ChalanOptionsAPIView, ChalanUpdateAPIView)
from .delivery_api import (CourierDetailAPIView, CourierListCreateAPIView, DeliveryAssignmentAPIView,
                           DeliveryDetailAPIView, DeliveryListAPIView, DeliveryTransitionAPIView)
from .packing_slip_api import PackingSlipGenerateAPIView, PackingSlipListAPIView
from .permissions import CHALAN_EDIT
from .return_api import (SalesReturnCompleteAPIView, SalesReturnDetailAPIView, SalesReturnListAPIView,
                         SalesReturnListCreateAPIView, SalesReturnOptionsAPIView, SalesReturnUpdateAPIView)


app_name = 'sale'

urlpatterns = [
    path('pos/catalog/', pos_views.catalog, name='pos-catalog'),
    path('pos/customers/', pos_views.customers, name='pos-customers'),
    path('pos/quote/', pos_views.quote, name='pos-quote'),
    path('pos/draft/', pos_views.write_ticket, {'checkout': False}, name='pos-draft'),
    path('pos/checkout/', pos_views.write_ticket, {'checkout': True}, name='pos-checkout'),
    path('pos/history/', pos_views.history, name='pos-history'),
    path('pos/tickets/<uuid:key>/', pos_views.ticket_detail, name='pos-ticket'),
    path('coupons/', instrument_views.collection, {'is_card': False}, name='coupons'),
    path('gift-cards/', instrument_views.collection, {'is_card': True}, name='gift-cards'),
    path('coupons/validate/', instrument_views.validate_instrument, {'is_card': False}, name='coupon-validate'),
    path('gift-cards/validate/', instrument_views.validate_instrument, {'is_card': True}, name='gift-card-validate'),
    path('coupons/deactivate/', instrument_views.manage_instrument, {'is_card': False, 'action': 'deactivate'}, name='coupon-deactivate'),
    path('gift-cards/deactivate/', instrument_views.manage_instrument, {'is_card': True, 'action': 'deactivate'}, name='gift-card-deactivate'),
    path('gift-cards/<int:pk>/recharge/', instrument_views.manage_instrument, {'is_card': True, 'action': 'recharge'}, name='gift-card-recharge'),
    path('gift-cards/<int:pk>/expiry/', instrument_views.manage_instrument, {'is_card': True, 'action': 'expiry'}, name='gift-card-expiry'),
    path('sales-orders/complete/', SaleCompleteAPIView.as_view(), name='sales-order-complete'),
    path('sales-orders/edit-options/', SalesEditOptionsAPIView.as_view(), name='sales-edit-options'),
    path('sales-orders/options/', SalesOptionsAPIView.as_view(), name='sale-options'),
    path('sales-orders/import/', SalesOrderImportAPIView.as_view(), name='sales-order-import'),
    path('sales-orders/import/options/', SalesImportOptionsAPIView.as_view(), name='sales-import-options'),
    path('sales-orders/', SalesOrderListCreateAPIView.as_view(), name='sales-order-list'),
    path('sales-orders/list/', SalesOrderListAPIView.as_view(), name='sales-order-list-view'),
    path('sales-orders/<int:pk>/', SalesOrderDetailAPIView.as_view(), name='sales-order-detail'),
    path('sales-orders/<int:pk>/update/', SalesOrderUpdateAPIView.as_view(), name='sales-order-update'),
    path('sales-order-items/', SalesOrderItemListCreateAPIView.as_view(), name='sales-order-item-list'),
    path('sales-order-items/list/', SalesOrderItemListAPIView.as_view(), name='sales-order-item-list-view'),
    path('sales-order-items/<int:pk>/', SalesOrderItemDetailAPIView.as_view(), name='sales-order-item-detail'),
    path('sales-order-items/<int:pk>/update/', SalesOrderItemUpdateAPIView.as_view(), name='sales-order-item-update'),
    path('chalans/', ChalanListCreateAPIView.as_view(), name='chalan-list'),
    path('chalans/list/', ChalanListAPIView.as_view(), name='chalan-list-view'),
    path('chalans/options/', ChalanOptionsAPIView.as_view(), name='chalan-options'),
    path('chalans/edit-options/', ChalanOptionsAPIView.as_view(required_permissions=CHALAN_EDIT), name='chalan-edit-options'),
    path('chalans/<int:pk>/', ChalanDetailAPIView.as_view(), name='chalan-detail'),
    path('chalans/<int:pk>/update/', ChalanUpdateAPIView.as_view(), name='chalan-update'),
    path('chalans/<int:pk>/packing-slip/', PackingSlipGenerateAPIView.as_view(), name='packing-slip-generate'),
    path('packing-slips/', PackingSlipListAPIView.as_view(), name='packing-slip-list'),
    path('couriers/', CourierListCreateAPIView.as_view(), name='courier-list'),
    path('couriers/<int:pk>/', CourierDetailAPIView.as_view(), name='courier-detail'),
    path('chalans/<int:pk>/delivery/', DeliveryAssignmentAPIView.as_view(), name='delivery-assign'),
    path('deliveries/', DeliveryListAPIView.as_view(), name='delivery-list'),
    path('deliveries/<int:pk>/', DeliveryDetailAPIView.as_view(), name='delivery-detail'),
    path('deliveries/<int:pk>/status/', DeliveryTransitionAPIView.as_view(), name='delivery-status'),
    path('sales-returns/', SalesReturnListCreateAPIView.as_view(), name='sales-return-list'),
    path('sales-returns/list/', SalesReturnListAPIView.as_view(), name='sales-return-list-view'),
    path('sales-returns/options/', SalesReturnOptionsAPIView.as_view(), name='sales-return-options'),
    path('sales-returns/complete/', SalesReturnCompleteAPIView.as_view(), name='sales-return-complete'),
    path('sales-returns/<int:pk>/', SalesReturnDetailAPIView.as_view(), name='sales-return-detail'),
    path('sales-returns/<int:pk>/update/', SalesReturnUpdateAPIView.as_view(), name='sales-return-update'),
    path('sales-returns/<int:pk>/options/', SalesReturnOptionsAPIView.as_view(), name='sales-return-edit-options'),
    path('quotations/', QuotationListCreateAPIView.as_view(), name='quotation-list'),
    path('quotations/options/', QuotationOptionsAPIView.as_view(), name='quotation-options'),
    path('quotations/edit-options/', QuotationOptionsAPIView.as_view(edit=True), name='quotation-edit-options'),
    path('quotations/<int:pk>/', QuotationDetailAPIView.as_view(), name='quotation-detail'),
    path('quotations/<int:pk>/<str:action>/', QuotationActionAPIView.as_view(), name='quotation-action'),
]
