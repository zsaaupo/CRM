from django.urls import path

from .api_views import (
    LeadDetailAPIView, LeadEditOptionsAPIView, LeadListCreateAPIView, LeadOptionsAPIView,
    PartyDetailAPIView, PartyListAPIView, PartyListCreateAPIView, PartyUpdateAPIView,
)


app_name = 'people'

urlpatterns = [
    path('parties/', PartyListCreateAPIView.as_view(), name='party-list'),
    path('parties/list/', PartyListAPIView.as_view(), name='party-list-view'),
    path('parties/<int:pk>/', PartyDetailAPIView.as_view(), name='party-detail'),
    path('parties/<int:pk>/update/', PartyUpdateAPIView.as_view(), name='party-update'),
    path('leads/', LeadListCreateAPIView.as_view(), name='lead-list'),
    path('leads/options/', LeadOptionsAPIView.as_view(), name='lead-options'),
    path('leads/<int:pk>/', LeadDetailAPIView.as_view(), name='lead-detail'),
    path('leads/<int:pk>/options/', LeadEditOptionsAPIView.as_view(), name='lead-edit-options'),
]
