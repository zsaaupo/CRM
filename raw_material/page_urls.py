from django.urls import path

from .page_views import RawMaterialCreatePageView, RawMaterialDetailPageView, RawMaterialEditPageView, RawMaterialListPageView


app_name = 'raw_material_pages'

urlpatterns = [
    path('materials/', RawMaterialListPageView.as_view(), name='material-list'),
    path('materials/add/', RawMaterialCreatePageView.as_view(), name='add-material'),
    path('materials/<int:pk>/', RawMaterialDetailPageView.as_view(), name='material-detail'),
    path('materials/<int:pk>/edit/', RawMaterialEditPageView.as_view(), name='edit-material'),
]
