from django.urls import path

from .page_views import (SalesCreatePageView, SalesDetailPageView, SalesEditPageView, SalesListPageView,
                         SalesReturnCreatePageView, SalesReturnDetailPageView, SalesReturnEditPageView,
                         SalesReturnListPageView)


app_name = 'sale_pages'

urlpatterns = [
    path('sales-orders/<int:pk>/edit/', SalesEditPageView.as_view(), name='edit-sale'),
    path('sales-orders/', SalesListPageView.as_view(), name='sale-list'),
    path('sales-orders/add/', SalesCreatePageView.as_view(), name='create-sale'),
    path('sales-orders/<int:pk>/', SalesDetailPageView.as_view(), name='sale-detail'),
    path('sales-returns/', SalesReturnListPageView.as_view(), name='return-list'),
    path('sales-returns/add/', SalesReturnCreatePageView.as_view(), name='create-return'),
    path('sales-returns/<int:pk>/', SalesReturnDetailPageView.as_view(), name='return-detail'),
    path('sales-returns/<int:pk>/edit/', SalesReturnEditPageView.as_view(), name='edit-return'),
]
