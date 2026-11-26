from django.urls import path

from .api_views import (
    RawMaterialDetailAPIView, RawMaterialEditOptionsAPIView, RawMaterialListAPIView, RawMaterialListCreateAPIView,
    RawMaterialOptionsAPIView, RawMaterialUpdateAPIView,
)


app_name = 'raw_material'

urlpatterns = [
    path('materials/', RawMaterialListCreateAPIView.as_view(), name='material-list'),
    path('materials/list/', RawMaterialListAPIView.as_view(), name='material-list-view'),
    path('materials/options/', RawMaterialOptionsAPIView.as_view(), name='material-options'),
    path('materials/<int:pk>/', RawMaterialDetailAPIView.as_view(), name='material-detail'),
    path('materials/<int:pk>/update/', RawMaterialUpdateAPIView.as_view(), name='material-update'),
    path('materials/<int:pk>/options/', RawMaterialEditOptionsAPIView.as_view(), name='material-edit-options'),
]
