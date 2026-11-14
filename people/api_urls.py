from django.urls import path

from .api_views import PartyDetailAPIView, PartyListAPIView, PartyListCreateAPIView, PartyUpdateAPIView


app_name = 'people'

urlpatterns = [
    path('parties/', PartyListCreateAPIView.as_view(), name='party-list'),
    path('parties/list/', PartyListAPIView.as_view(), name='party-list-view'),
    path('parties/<int:pk>/', PartyDetailAPIView.as_view(), name='party-detail'),
    path('parties/<int:pk>/update/', PartyUpdateAPIView.as_view(), name='party-update'),
]
