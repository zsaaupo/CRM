from django.urls import path
from .count_views import CountSheetAPI, StockCountAPI
from .adjustments import AdjustmentAPI
from .adjustments import AdjustmentAPI
from .activity import REPORTS

from .api_views import (
    MovementListAPIView, TransferStageAPIView,
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
    path('adjustments/', AdjustmentAPI.as_view(), name='adjustments'),
    path('adjustments/', AdjustmentAPI.as_view(), name='adjustments'),
    path('transfers/<int:pk>/dispatch/', TransferStageAPIView.as_view(action='dispatch'), name='transfer-dispatch'),
    path('transfers/<int:pk>/receive/', TransferStageAPIView.as_view(action='receive'), name='transfer-receive'),
    path('counts/', StockCountAPI.as_view(), name='count-list'),
    path('counts/<int:pk>/', StockCountAPI.as_view(), name='count-detail'),
    path('counts/<int:pk>/sheet/', CountSheetAPI.as_view(), name='count-sheet'),
    path('counts/<int:pk>/finalize/', StockCountAPI.as_view(), name='count-finalize'),
    path('movements/', MovementListAPIView.as_view(), name='movement-list'),
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
