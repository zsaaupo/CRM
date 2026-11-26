from django.urls import path
from .activity import REPORTS
from .adjustment_api import (
    AdjustmentCompleteAPIView, AdjustmentEditOptionsAPIView, AdjustmentOptionsAPIView, StockAdjustmentDetailAPIView,
    StockAdjustmentListAPIView, StockAdjustmentListCreateAPIView, StockAdjustmentUpdateAPIView,
)
from .stock_count_api import (
    StockCountDetailAPIView, StockCountFileAPIView, StockCountFinalizeAPIView, StockCountListAPIView,
    StockCountOptionsAPIView, StockCountReportAPIView,
)

from .api_views import (
    StockChartAPIView, StockDetailAPIView, StockListAPIView,
    RoleWarehouseAccessDetailAPIView, RoleWarehouseAccessListAPIView,
    RoleWarehouseAccessListCreateAPIView, RoleWarehouseAccessUpdateAPIView,
    RoleWarehouseAccessOptionsAPIView,
    WareHouseDetailAPIView, WareHouseListAPIView, WareHouseListCreateAPIView,
    WareHouseUpdateAPIView,
    WarehouseTransferDetailAPIView, WarehouseTransferImportAPIView, WarehouseTransferListAPIView,
    WarehouseTransferListCreateAPIView, WarehouseTransferUpdateAPIView,
    TransferCompleteAPIView, TransferOptionsAPIView, TransferEditOptionsAPIView,
)


app_name = 'warehouse'

urlpatterns = [
    *[path(f'reports/<int:warehouse_id>/{key}/', view.as_view(), name=f'report-{key}')
      for key, (_, view, _) in REPORTS.items()],
    path('stock-counts/', StockCountListAPIView.as_view(), name='stock-count-list'),
    path('stock-counts/options/', StockCountOptionsAPIView.as_view(), name='stock-count-options'),
    path('stock-counts/<int:pk>/', StockCountDetailAPIView.as_view(), name='stock-count-detail'),
    path('stock-counts/<int:pk>/initial-file/', StockCountFileAPIView.as_view(), name='stock-count-initial-file'),
    path('stock-counts/<int:pk>/finalize/', StockCountFinalizeAPIView.as_view(), name='stock-count-finalize'),
    path('stock-counts/<int:pk>/final-file/', StockCountFileAPIView.as_view(final=True), name='stock-count-final-file'),
    path('stock-counts/<int:pk>/report/', StockCountReportAPIView.as_view(), name='stock-count-report'),
    path('adjustments/options/', AdjustmentOptionsAPIView.as_view(), name='adjustment-options'),
    path('adjustments/edit-options/', AdjustmentEditOptionsAPIView.as_view(), name='adjustment-edit-options'),
    path('adjustments/complete/', AdjustmentCompleteAPIView.as_view(), name='adjustment-complete'),
    path('adjustments/', StockAdjustmentListCreateAPIView.as_view(), name='adjustment-list'),
    path('adjustments/list/', StockAdjustmentListAPIView.as_view(), name='adjustment-list-view'),
    path('adjustments/<int:pk>/', StockAdjustmentDetailAPIView.as_view(), name='adjustment-detail'),
    path('adjustments/<int:pk>/update/', StockAdjustmentUpdateAPIView.as_view(), name='adjustment-update'),
    path('transfers/options/', TransferOptionsAPIView.as_view(), name='transfer-options'),
    path('transfers/edit-options/', TransferEditOptionsAPIView.as_view(), name='transfer-edit-options'),
    path('transfers/import/', WarehouseTransferImportAPIView.as_view(), name='transfer-import'),
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
    path('stocks/chart/', StockChartAPIView.as_view(), name='stock-chart'),
    path('stocks/<int:pk>/', StockDetailAPIView.as_view(), name='stock-detail'),
    path('warehouses/', WareHouseListCreateAPIView.as_view(), name='warehouse-list'),
    path('warehouses/list/', WareHouseListAPIView.as_view(), name='warehouse-list-view'),
    path('warehouses/<int:pk>/', WareHouseDetailAPIView.as_view(), name='warehouse-detail'),
    path('warehouses/<int:pk>/update/', WareHouseUpdateAPIView.as_view(), name='warehouse-update'),
]
