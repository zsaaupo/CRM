from django.urls import reverse

from account.page_views import ProtectedAccountPageView


class TradeStatusPageView(ProtectedAccountPageView):
    mode = 'list'

    def get_permission_required(self):
        permissions = ['common.view_tradestatus']
        if self.mode == 'add':
            permissions.append('common.add_tradestatus')
        elif self.mode == 'edit':
            permissions.append('common.change_tradestatus')
        return permissions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        heading = 'Trade status list' if self.mode == 'list' else f'{self.mode.capitalize()} trade status'
        context.update({
            'page_section': 'settings', 'settings_key': 'trade-status', 'section_label': 'Settings',
            'page_mode': self.mode, 'page_label': heading, 'form_heading': heading,
            'resource': {'singular': 'trade status', 'plural': 'trade statuses'},
            'list_url': reverse('common_pages:trade-status-list'),
            'add_url': reverse('common_pages:add-trade-status'),
            'list_api_url': reverse('common:trade-status-list-view'),
            'create_api_url': reverse('common:trade-status-list'),
            'fields_template': 'inventory/fields/status.html',
            'save_label': 'Save changes' if self.mode == 'edit' else 'Add trade status',
            'can_add': user.has_perm('common.add_tradestatus'),
            'can_delete': user.has_perm('common.delete_tradestatus'),
            'columns': [
                {'key': key, 'label': label, 'type': kind}
                for key, label, kind in [
                    ('name', 'Name', 'text'), ('code', 'Code', 'text'),
                    ('description', 'Description', 'text'), ('is_active', 'Status', 'status'),
                ]
            ],
        })
        can_change = user.has_perm('common.change_tradestatus')
        if can_change or context['can_delete']:
            context['columns'].append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
        if can_change:
            context['edit_url_template'] = reverse('common_pages:edit-trade-status', args=[0])
        if context['can_delete']:
            context['delete_url_template'] = reverse('common:trade-status-detail', args=[0])
        if self.mode == 'edit':
            context.update({
                'lock_code': True, 'form_method': 'PATCH',
                'detail_api_url': reverse('common:trade-status-detail', args=[self.kwargs['pk']]),
                'create_api_url': reverse('common:trade-status-update', args=[self.kwargs['pk']]),
            })
        return context


class TradeStatusListPageView(TradeStatusPageView):
    template_name = 'inventory/list.html'


class TradeStatusAddPageView(TradeStatusPageView):
    template_name = 'inventory/add.html'
    mode = 'add'


class TradeStatusEditPageView(TradeStatusAddPageView):
    mode = 'edit'
