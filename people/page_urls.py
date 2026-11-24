from django.urls import path

from .page_views import (
    LeadAddPageView, LeadDetailPageView, LeadEditPageView, LeadPageView,
    PartyAddPageView, PartyDetailPageView, PartyEditPageView, PartyListPageView,
)


app_name = 'people_pages'

urlpatterns = [
    path('leads/', LeadPageView.as_view(), name='lead-list'),
    path('leads/add/', LeadAddPageView.as_view(), name='add-lead'),
    path('leads/<int:pk>/', LeadDetailPageView.as_view(), name='lead-detail'),
    path('leads/<int:pk>/edit/', LeadEditPageView.as_view(), name='edit-lead'),
]
for role in ('supplier', 'customer'):
    urlpatterns += [
        path(f'{role}s/', PartyListPageView.as_view(party_role=role), name=f'{role}-list'),
        path(f'{role}s/add/', PartyAddPageView.as_view(party_role=role), name=f'add-{role}'),
        path(f'{role}s/<int:pk>/', PartyDetailPageView.as_view(party_role=role), name=f'{role}-detail'),
        path(f'{role}s/<int:pk>/edit/', PartyEditPageView.as_view(party_role=role), name=f'edit-{role}'),
    ]
