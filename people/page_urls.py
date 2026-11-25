from django.urls import path

from .page_views import (
    BillerAddPageView, BillerEditPageView, BillerPageView,
    CustomerGroupAddPageView, CustomerGroupEditPageView, CustomerGroupPageView,
    LeadAddPageView, LeadDetailPageView, LeadEditPageView, LeadPageView,
    PartyAddPageView, PartyDetailPageView, PartyEditPageView, PartyListPageView,
)


app_name = 'people_pages'

urlpatterns = [
    path('leads/', LeadPageView.as_view(), name='lead-list'),
    path('leads/add/', LeadAddPageView.as_view(), name='add-lead'),
    path('leads/<int:pk>/', LeadDetailPageView.as_view(), name='lead-detail'),
    path('leads/<int:pk>/edit/', LeadEditPageView.as_view(), name='edit-lead'),
    path('billers/', BillerPageView.as_view(), name='biller-list'),
    path('billers/add/', BillerAddPageView.as_view(), name='add-biller'),
    path('billers/<int:pk>/edit/', BillerEditPageView.as_view(), name='edit-biller'),
    path('customer-groups/', CustomerGroupPageView.as_view(), name='customer-group-list'),
    path('customer-groups/add/', CustomerGroupAddPageView.as_view(), name='add-customer-group'),
    path('customer-groups/<int:pk>/edit/', CustomerGroupEditPageView.as_view(), name='edit-customer-group'),
]
for role in ('supplier', 'customer'):
    urlpatterns += [
        path(f'{role}s/', PartyListPageView.as_view(party_role=role), name=f'{role}-list'),
        path(f'{role}s/add/', PartyAddPageView.as_view(party_role=role), name=f'add-{role}'),
        path(f'{role}s/<int:pk>/', PartyDetailPageView.as_view(party_role=role), name=f'{role}-detail'),
        path(f'{role}s/<int:pk>/edit/', PartyEditPageView.as_view(party_role=role), name=f'edit-{role}'),
    ]
