from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from account.page_views import ProtectedAccountPageView


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
        if self.party_role not in ('supplier', 'customer'):
            raise ImproperlyConfigured('Declare a supplier or customer page role.')
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
