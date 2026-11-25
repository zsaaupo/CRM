from django.urls import path
from .count_views import StockCountPage

from .page_views import (
    MovementListPageView,
    StockDetailPageView, StockListPageView, WarehouseReportPageView,
    WareHouseAddPageView, WareHouseEditPageView, WareHouseListPageView,
    TransferListPageView, TransferAddPageView, TransferEditPageView, TransferDetailPageView,
)


app_name = 'warehouse_pages'

urlpatterns = [
    path('counts/', StockCountPage.as_view(), name='count-list'),
    path('counts/<int:pk>/', StockCountPage.as_view(), name='count-detail'),
    path('movements/', MovementListPageView.as_view(), name='movement-list'),
    path('report/', WarehouseReportPageView.as_view(), name='report'),
    path('transfers/', TransferListPageView.as_view(), name='transfer-list'),
    path('transfers/add/', TransferAddPageView.as_view(), name='add-transfer'),
    path('transfers/<int:pk>/edit/', TransferEditPageView.as_view(), name='edit-transfer'),
    path('transfers/<int:pk>/', TransferDetailPageView.as_view(), name='transfer-detail'),
    path('stocks/', StockListPageView.as_view(), name='stock-list'),
    path('stocks/<int:pk>/', StockDetailPageView.as_view(), name='stock-detail'),
    path('warehouses/', WareHouseListPageView.as_view(), name='warehouse-list'),
    path('warehouses/add/', WareHouseAddPageView.as_view(), name='add-warehouse'),
    path('warehouses/<int:pk>/edit/', WareHouseEditPageView.as_view(), name='edit-warehouse'),
]
