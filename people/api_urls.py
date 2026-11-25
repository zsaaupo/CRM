from django.urls import path

from .api_views import (
    BillerDetailAPIView, BillerListCreateAPIView, BillerOptionsAPIView, CustomerCreateLoginAPIView,
    CustomerGroupDetailAPIView, CustomerGroupListCreateAPIView, CustomerPointLedgerListAPIView,
    LeadDetailAPIView, LeadEditOptionsAPIView, LeadListCreateAPIView, LeadOptionsAPIView,
    PartyDetailAPIView, PartyListAPIView, PartyListCreateAPIView, PartyUpdateAPIView,
)


app_name = 'people'

urlpatterns = [
    path('parties/', PartyListCreateAPIView.as_view(), name='party-list'),
    path('parties/list/', PartyListAPIView.as_view(), name='party-list-view'),
    path('parties/<int:pk>/', PartyDetailAPIView.as_view(), name='party-detail'),
    path('parties/<int:pk>/update/', PartyUpdateAPIView.as_view(), name='party-update'),
    path('parties/<int:pk>/create-login/', CustomerCreateLoginAPIView.as_view(), name='party-create-login'),
    path('billers/', BillerListCreateAPIView.as_view(), name='biller-list'),
    path('billers/options/', BillerOptionsAPIView.as_view(), name='biller-options'),
    path('billers/<int:pk>/', BillerDetailAPIView.as_view(), name='biller-detail'),
    path('customer-groups/', CustomerGroupListCreateAPIView.as_view(), name='customer-group-list'),
    path('customer-groups/<int:pk>/', CustomerGroupDetailAPIView.as_view(), name='customer-group-detail'),
    path('leads/', LeadListCreateAPIView.as_view(), name='lead-list'),
    path('leads/options/', LeadOptionsAPIView.as_view(), name='lead-options'),
    path('leads/<int:pk>/', LeadDetailAPIView.as_view(), name='lead-detail'),
    path('leads/<int:pk>/options/', LeadEditOptionsAPIView.as_view(), name='lead-edit-options'),
    path('customers/<int:customer_id>/points/', CustomerPointLedgerListAPIView.as_view(), name='customer-points'),
]
