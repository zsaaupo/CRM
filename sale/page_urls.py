from django.urls import path

from .page_views import SalesCreatePageView, SalesDetailPageView, SalesEditPageView, SalesListPageView


app_name = 'sale_pages'

urlpatterns = [
    path('sales-orders/<int:pk>/edit/', SalesEditPageView.as_view(), name='edit-sale'),
    path('sales-orders/', SalesListPageView.as_view(), name='sale-list'),
    path('sales-orders/add/', SalesCreatePageView.as_view(), name='create-sale'),
    path('sales-orders/<int:pk>/', SalesDetailPageView.as_view(), name='sale-detail'),
]
