from django.urls import path

from .api_views import (
    BrandUpdateAPIView, CurrencyUpdateAPIView, ProductCategoryUpdateAPIView,
    ProductTypeUpdateAPIView, ProductUpdateAPIView, StatusUpdateAPIView, UnitOfMeasureUpdateAPIView,
    ProductOptionsAPIView, ProductEditOptionsAPIView,
    CurrencyDetailAPIView, CurrencyListAPIView, CurrencyListCreateAPIView,
    BrandDetailAPIView, BrandListCreateAPIView,
    ProductCategoryDetailAPIView, ProductCategoryListCreateAPIView,
    ProductDetailAPIView, ProductListCreateAPIView,
    ProductTypeDetailAPIView, ProductTypeListCreateAPIView,
    StatusDetailAPIView, StatusListCreateAPIView,
    UnitOfMeasureDetailAPIView, UnitOfMeasureListCreateAPIView,
    BrandListAPIView, ProductCategoryListAPIView, ProductListAPIView,
    ProductTypeListAPIView, StatusListAPIView, UnitOfMeasureListAPIView,
)


app_name = 'inventory'

urlpatterns = [
    path('products/<int:pk>/options/', ProductEditOptionsAPIView.as_view(), name='product-edit-options'),
    path('products/<int:pk>/update/', ProductUpdateAPIView.as_view(), name='product-update'),
    path('categories/<int:pk>/update/', ProductCategoryUpdateAPIView.as_view(), name='category-update'),
    path('brands/<int:pk>/update/', BrandUpdateAPIView.as_view(), name='brand-update'),
    path('units/<int:pk>/update/', UnitOfMeasureUpdateAPIView.as_view(), name='unit-update'),
    path('product-types/<int:pk>/update/', ProductTypeUpdateAPIView.as_view(), name='product-type-update'),
    path('statuses/<int:pk>/update/', StatusUpdateAPIView.as_view(), name='status-update'),
    path('currencies/<int:pk>/update/', CurrencyUpdateAPIView.as_view(), name='currency-update'),
    path('products/options/', ProductOptionsAPIView.as_view(), name='product-options'),
    path('currencies/', CurrencyListCreateAPIView.as_view(), name='currency-list'),
    path('currencies/list/', CurrencyListAPIView.as_view(), name='currency-list-view'),
    path('currencies/<int:pk>/', CurrencyDetailAPIView.as_view(), name='currency-detail'),
    path('products/', ProductListCreateAPIView.as_view(), name='product-list'),
    path('products/list/', ProductListAPIView.as_view(), name='product-list-view'),
    path('products/<int:pk>/', ProductDetailAPIView.as_view(), name='product-detail'),
    path('categories/', ProductCategoryListCreateAPIView.as_view(), name='category-list'),
    path('categories/list/', ProductCategoryListAPIView.as_view(), name='category-list-view'),
    path('categories/<int:pk>/', ProductCategoryDetailAPIView.as_view(), name='category-detail'),
    path('brands/', BrandListCreateAPIView.as_view(), name='brand-list'),
    path('brands/list/', BrandListAPIView.as_view(), name='brand-list-view'),
    path('brands/<int:pk>/', BrandDetailAPIView.as_view(), name='brand-detail'),
    path('units/', UnitOfMeasureListCreateAPIView.as_view(), name='unit-list'),
    path('units/list/', UnitOfMeasureListAPIView.as_view(), name='unit-list-view'),
    path('units/<int:pk>/', UnitOfMeasureDetailAPIView.as_view(), name='unit-detail'),
    path('product-types/', ProductTypeListCreateAPIView.as_view(), name='product-type-list'),
    path('product-types/list/', ProductTypeListAPIView.as_view(), name='product-type-list-view'),
    path('product-types/<int:pk>/', ProductTypeDetailAPIView.as_view(), name='product-type-detail'),
    path('statuses/', StatusListCreateAPIView.as_view(), name='status-list'),
    path('statuses/list/', StatusListAPIView.as_view(), name='status-list-view'),
    path('statuses/<int:pk>/', StatusDetailAPIView.as_view(), name='status-detail'),
]
