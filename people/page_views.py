from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from account.page_views import ProtectedAccountPageView


class LeadPageView(ProtectedAccountPageView):
    template_name = 'inventory/list.html'
    mode = 'list'

    def get_permission_required(self):
        permissions = ['people.view_lead']
        action = {'add': 'add', 'edit': 'change'}.get(self.mode)
        if action:
            permissions.append(f'people.{action}_lead')
        return permissions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        pk = self.kwargs.get('pk')
        heading = {'list': 'Lead list', 'add': 'Add lead', 'edit': 'Edit lead', 'detail': 'Lead details'}[self.mode]
        context.update({
            'page_section': 'lead', 'section_label': 'Lead', 'page_mode': self.mode,
            'page_label': heading, 'list_heading': heading, 'form_heading': heading,
            'resource': {'singular': 'lead', 'plural': 'leads'},
            'list_url': reverse('people_pages:lead-list'),
            'add_url': reverse('people_pages:add-lead'),
            'hide_catalog_hint': True, 'hide_list_hint': True,
        })
        if self.mode == 'list':
            context.update({
                'can_add': user.has_perm('people.add_lead'),
                'list_api_url': reverse('people:lead-list'),
                'view_url_template': reverse('people_pages:lead-detail', args=[0]),
                'list_description': 'Search by name, company, or contact information.',
                'columns': [
                    {'key': 'name', 'label': 'Name', 'type': 'identity'},
                    {'key': 'company', 'label': 'Company'},
                    {'key': 'status_label', 'label': 'Status'},
                    {'key': 'assigned_to_name', 'label': 'Assigned to'},
                    {'key': 'expected_value', 'label': 'Expected value'},
                    {'key': 'follow_up_date', 'label': 'Follow-up date'},
                    {'key': 'expected_close_date', 'label': 'Expected close date'},
                    {'key': 'actions', 'label': 'Actions'},
                ],
            })
            if user.has_perm('people.change_lead'):
                context['edit_url_template'] = reverse('people_pages:edit-lead', args=[0])
        if self.mode in ('add', 'edit'):
            context.update({
                'fields_template': 'people/lead_fields.html',
                'create_api_url': reverse('people:lead-list'),
                'options_api_url': reverse('people:lead-options'),
                'save_label': 'Add lead',
            })
        if self.mode in ('detail', 'edit'):
            context['detail_api_url'] = reverse('people:lead-detail', args=[pk])
        if self.mode == 'edit':
            context.update({
                'create_api_url': context['detail_api_url'],
                'options_api_url': reverse('people:lead-edit-options', args=[pk]),
                'form_method': 'PATCH', 'save_label': 'Save changes',
            })
        if self.mode == 'detail' and user.has_perm('people.change_lead'):
            context['edit_url'] = reverse('people_pages:edit-lead', args=[pk])
        return context


class LeadAddPageView(LeadPageView):
    template_name = 'inventory/add.html'
    mode = 'add'


class LeadEditPageView(LeadAddPageView):
    mode = 'edit'


class LeadDetailPageView(LeadPageView):
    template_name = 'people/lead_detail.html'
    mode = 'detail'


class PartyPageView(ProtectedAccountPageView):
    party_role = None
    mode = 'list'

    def get_permission_required(self):
        permissions = ['people.view_party']
        if self.mode == 'add':
            permissions.append('people.add_party')
        elif self.mode == 'edit':
            permissions.append('people.change_party')
        return permissions

    def get_context_data(self, **kwargs):
        if self.party_role not in ('supplier', 'customer', 'biller'):
            raise ImproperlyConfigured('Declare a supplier, customer, or biller page role.')
        context = super().get_context_data(**kwargs)
        role = self.party_role
        user = self.request.user
        role_query = f'?is_{role}=true'
        heading = f'{role.capitalize()} list' if self.mode == 'list' else (
            f'{role.capitalize()} details' if self.mode == 'detail' else f'{self.mode.capitalize()} {role}')
        context.update({
            'page_section': 'people', 'section_label': 'People', 'people_role': role,
            'page_mode': self.mode, 'page_label': heading, 'form_heading': heading,
            'resource': {'singular': role, 'plural': f'{role}s'},
            'list_url': reverse(f'people_pages:{role}-list'),
            'add_url': reverse(f'people_pages:add-{role}'),
            'list_api_url': reverse('people:party-list-view') + role_query,
            'create_api_url': reverse('people:party-list'),
            'fields_template': 'people/fields.html',
            'save_label': 'Save changes' if self.mode == 'edit' else f'Add {role}',
            'can_add': user.has_perm('people.add_party'),
            'can_delete': user.has_perm('people.delete_party'), 'soft_delete': True,
            'list_description': f'Contact information and status for your {role}s.',
            'form_description': f'Keep your {role} contact and business details up to date.',
            'view_url_template': reverse(f'people_pages:{role}-detail', args=[0]),
            'columns': [
                {'key': key, 'label': label, 'type': kind}
                for key, label, kind in [
                    ('name', 'Name', 'identity'), ('code', 'Code', 'text'),
                    ('contact_person', 'Contact person', 'text'), ('phone', 'Phone', 'text'),
                    ('email', 'Email', 'text'), ('is_active', 'Status', 'status'),
                    ('actions', 'Actions', 'edit'),
                ]
            ],
        })
        if user.has_perm('people.change_party'):
            context['edit_url_template'] = reverse(f'people_pages:edit-{role}', args=[0])
        if context['can_delete']:
            context['delete_url_template'] = reverse('people:party-detail', args=[0]) + role_query
        if self.mode in ('edit', 'detail'):
            context['detail_api_url'] = reverse('people:party-detail', args=[self.kwargs['pk']]) + role_query
        if self.mode == 'edit':
            context.update({
                'form_method': 'PATCH',
                'create_api_url': reverse('people:party-update', args=[self.kwargs['pk']]) + role_query,
            })
        if self.mode == 'detail' and user.has_perm('people.change_party'):
            context['edit_url'] = reverse(f'people_pages:edit-{role}', args=[self.kwargs['pk']])
        return context


class PartyListPageView(PartyPageView):
    template_name = 'inventory/list.html'


class PartyAddPageView(PartyPageView):
    template_name = 'inventory/add.html'
    mode = 'add'


class PartyEditPageView(PartyAddPageView):
    mode = 'edit'


class PartyDetailPageView(PartyPageView):
    template_name = 'people/detail.html'
    mode = 'detail'
