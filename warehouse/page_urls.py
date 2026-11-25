from django.urls import path

from .page_views import (
    AdjustmentAddPageView, AdjustmentDetailPageView, AdjustmentEditPageView, AdjustmentListPageView,
    StockChartPageView, StockDetailPageView, StockListPageView, WarehouseReportPageView,
    WareHouseAddPageView, WareHouseEditPageView, WareHouseListPageView,
    TransferListPageView, TransferAddPageView, TransferEditPageView, TransferDetailPageView,
)


app_name = 'warehouse_pages'

urlpatterns = [
    path('report/', WarehouseReportPageView.as_view(), name='report'),
    path('adjustments/', AdjustmentListPageView.as_view(), name='adjustment-list'),
    path('adjustments/add/', AdjustmentAddPageView.as_view(), name='add-adjustment'),
    path('adjustments/<int:pk>/edit/', AdjustmentEditPageView.as_view(), name='edit-adjustment'),
    path('adjustments/<int:pk>/', AdjustmentDetailPageView.as_view(), name='adjustment-detail'),
    path('transfers/', TransferListPageView.as_view(), name='transfer-list'),
    path('transfers/add/', TransferAddPageView.as_view(), name='add-transfer'),
    path('transfers/<int:pk>/edit/', TransferEditPageView.as_view(), name='edit-transfer'),
    path('transfers/<int:pk>/', TransferDetailPageView.as_view(), name='transfer-detail'),
    path('stocks/', StockListPageView.as_view(), name='stock-list'),
    path('stocks/chart/', StockChartPageView.as_view(), name='stock-chart'),
    path('stocks/<int:pk>/', StockDetailPageView.as_view(), name='stock-detail'),
    path('warehouses/', WareHouseListPageView.as_view(), name='warehouse-list'),
    path('warehouses/add/', WareHouseAddPageView.as_view(), name='add-warehouse'),
    path('warehouses/<int:pk>/edit/', WareHouseEditPageView.as_view(), name='edit-warehouse'),
]
