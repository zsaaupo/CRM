from django.urls import path

from .page_views import PartyAddPageView, PartyDetailPageView, PartyEditPageView, PartyListPageView


app_name = 'people_pages'

urlpatterns = []
for role in ('supplier', 'customer'):
    urlpatterns += [
        path(f'{role}s/', PartyListPageView.as_view(party_role=role), name=f'{role}-list'),
        path(f'{role}s/add/', PartyAddPageView.as_view(party_role=role), name=f'add-{role}'),
        path(f'{role}s/<int:pk>/', PartyDetailPageView.as_view(party_role=role), name=f'{role}-detail'),
        path(f'{role}s/<int:pk>/edit/', PartyEditPageView.as_view(party_role=role), name=f'edit-{role}'),
    ]
