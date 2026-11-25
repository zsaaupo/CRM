from django.urls import path

from .api_views import (
    BOMApproveAPIView, BOMDetailAPIView, BOMListCreateAPIView,
    ProductionOrderCancelAPIView, ProductionOrderDetailAPIView, ProductionOrderListCreateAPIView,
    ProductionOrderPostAPIView, ProductionPreviewAPIView, RawMaterialDetailAPIView,
    RawMaterialListCreateAPIView, RawMaterialReceiveAPIView, RawMaterialStockListAPIView,
)


app_name = 'manufacturing'

urlpatterns = [
    path('raw-materials/', RawMaterialListCreateAPIView.as_view(), name='raw-material-list'),
    path('raw-materials/<int:pk>/', RawMaterialDetailAPIView.as_view(), name='raw-material-detail'),
    path('raw-materials/<int:pk>/receive/', RawMaterialReceiveAPIView.as_view(), name='raw-material-receive'),
    path('raw-material-stock/', RawMaterialStockListAPIView.as_view(), name='raw-material-stock-list'),
    path('boms/', BOMListCreateAPIView.as_view(), name='bom-list'),
    path('boms/<int:pk>/', BOMDetailAPIView.as_view(), name='bom-detail'),
    path('boms/<int:pk>/approve/', BOMApproveAPIView.as_view(), name='bom-approve'),
    path('production/preview/', ProductionPreviewAPIView.as_view(), name='production-preview'),
    path('production-orders/', ProductionOrderListCreateAPIView.as_view(), name='production-order-list'),
    path('production-orders/<int:pk>/', ProductionOrderDetailAPIView.as_view(), name='production-order-detail'),
    path('production-orders/<int:pk>/post/', ProductionOrderPostAPIView.as_view(), name='production-order-post'),
    path('production-orders/<int:pk>/cancel/', ProductionOrderCancelAPIView.as_view(), name='production-order-cancel'),
]
