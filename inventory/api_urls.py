"""Inventory JSON API routes."""

from django.urls import path

from .api_views import (
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
