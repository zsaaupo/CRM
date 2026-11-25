from django.urls import path

from .api_views import (
    OpeningStockPostAPIView, StockAdjustmentDetailAPIView, StockAdjustmentListCreateAPIView,
    StockAdjustmentPostAPIView, StockAdjustmentUpdateAPIView, StockBatchListAPIView,
    StockCountDetailAPIView, StockCountFinalFileAPIView, StockCountFinalizeAPIView,
    StockCountInitialFileAPIView, StockCountListCreateAPIView, StockSerialListAPIView,
    StockValuationChartAPIView, WarehouseTransferDispatchAPIView,
    WarehouseTransferImportCSVAPIView, WarehouseTransferReceiveAPIView,
)
from .activity import REPORTS

from .api_views import (
    StockDetailAPIView, StockListAPIView,
    RoleWarehouseAccessDetailAPIView, RoleWarehouseAccessListAPIView,
    RoleWarehouseAccessListCreateAPIView, RoleWarehouseAccessUpdateAPIView,
    RoleWarehouseAccessOptionsAPIView,
    WareHouseDetailAPIView, WareHouseListAPIView, WareHouseListCreateAPIView,
    WareHouseUpdateAPIView,
    WarehouseTransferListCreateAPIView, WarehouseTransferListAPIView, WarehouseTransferDetailAPIView,
    WarehouseTransferUpdateAPIView, TransferCompleteAPIView, TransferOptionsAPIView, TransferEditOptionsAPIView,
)


app_name = 'warehouse'

urlpatterns = [
    path('batches/', StockBatchListAPIView.as_view(), name='batch-list'),
    path('serials/', StockSerialListAPIView.as_view(), name='serial-list'),
    path('opening-stock/', OpeningStockPostAPIView.as_view(), name='opening-stock'),
    path('stock-chart/', StockValuationChartAPIView.as_view(), name='stock-chart'),
    path('adjustments/', StockAdjustmentListCreateAPIView.as_view(), name='adjustment-list'),
    path('adjustments/<int:pk>/', StockAdjustmentDetailAPIView.as_view(), name='adjustment-detail'),
    path('adjustments/<int:pk>/update/', StockAdjustmentUpdateAPIView.as_view(), name='adjustment-update'),
    path('adjustments/<int:pk>/post/', StockAdjustmentPostAPIView.as_view(), name='adjustment-post'),
    path('stock-counts/', StockCountListCreateAPIView.as_view(), name='stock-count-list'),
    path('stock-counts/<int:pk>/', StockCountDetailAPIView.as_view(), name='stock-count-detail'),
    path('stock-counts/<int:pk>/initial-file/', StockCountInitialFileAPIView.as_view(), name='stock-count-initial-file'),
    path('stock-counts/<int:pk>/final-file/', StockCountFinalFileAPIView.as_view(), name='stock-count-final-file'),
    path('stock-counts/<int:pk>/finalize/', StockCountFinalizeAPIView.as_view(), name='stock-count-finalize'),
    path('transfers/<int:pk>/dispatch/', WarehouseTransferDispatchAPIView.as_view(), name='transfer-dispatch'),
    path('transfers/<int:pk>/receive/', WarehouseTransferReceiveAPIView.as_view(), name='transfer-receive'),
    path('transfers/import-csv/', WarehouseTransferImportCSVAPIView.as_view(), name='transfer-import-csv'),
    *[path(f'reports/<int:warehouse_id>/{key}/', view.as_view(), name=f'report-{key}')
      for key, (_, view, _) in REPORTS.items()],
    path('transfers/options/', TransferOptionsAPIView.as_view(), name='transfer-options'),
    path('transfers/edit-options/', TransferEditOptionsAPIView.as_view(), name='transfer-edit-options'),
    path('transfers/complete/', TransferCompleteAPIView.as_view(), name='transfer-complete'),
    path('transfers/', WarehouseTransferListCreateAPIView.as_view(), name='transfer-list'),
    path('transfers/list/', WarehouseTransferListAPIView.as_view(), name='transfer-list-view'),
    path('transfers/<int:pk>/', WarehouseTransferDetailAPIView.as_view(), name='transfer-detail'),
    path('transfers/<int:pk>/update/', WarehouseTransferUpdateAPIView.as_view(), name='transfer-update'),
    path('role-warehouse-access/options/', RoleWarehouseAccessOptionsAPIView.as_view(), name='role-warehouse-access-options'),
    path('role-warehouse-access/', RoleWarehouseAccessListCreateAPIView.as_view(), name='role-warehouse-access-list'),
    path('role-warehouse-access/list/', RoleWarehouseAccessListAPIView.as_view(), name='role-warehouse-access-list-view'),
    path('role-warehouse-access/<int:pk>/', RoleWarehouseAccessDetailAPIView.as_view(), name='role-warehouse-access-detail'),
    path('role-warehouse-access/<int:pk>/update/', RoleWarehouseAccessUpdateAPIView.as_view(), name='role-warehouse-access-update'),
    path('stocks/list/', StockListAPIView.as_view(), name='stock-list'),
    path('stocks/<int:pk>/', StockDetailAPIView.as_view(), name='stock-detail'),
    path('warehouses/', WareHouseListCreateAPIView.as_view(), name='warehouse-list'),
    path('warehouses/list/', WareHouseListAPIView.as_view(), name='warehouse-list-view'),
    path('warehouses/<int:pk>/', WareHouseDetailAPIView.as_view(), name='warehouse-detail'),
    path('warehouses/<int:pk>/update/', WareHouseUpdateAPIView.as_view(), name='warehouse-update'),
]
