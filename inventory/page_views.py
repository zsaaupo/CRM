from django.core.exceptions import ImproperlyConfigured
from django.conf import settings
from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from .catalog import RESOURCE_MAP


class InventoryPageView(ProtectedAccountPageView):
    resource_key = None
    mode = 'list'

    @property
    def resource(self):
        if self.resource_key not in RESOURCE_MAP:
            raise ImproperlyConfigured('Declare a registered inventory resource.')
        return RESOURCE_MAP[self.resource_key]

    def get_permission_required(self):
        permissions = [self.resource.permission('view')]
        if self.mode == 'add':
            permissions.append(self.resource.permission('add'))
        elif self.mode == 'edit':
            permissions.append(self.resource.permission('change'))
        return permissions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        resource = self.resource
        context.update({
            'resource': resource,
            'page_section': 'inventory',
            'inventory_key': resource.key,
            'inventory_mode': self.mode,
            'page_label': f'Add {resource.singular}' if self.mode == 'add' else f'{resource.singular.capitalize()} list',
            'list_url': reverse(f'inventory_pages:{resource.key}-list'),
            'add_url': reverse(f'inventory_pages:add-{resource.key}'),
            'list_api_url': reverse(f'inventory:{resource.key}-list-view'),
            'create_api_url': reverse(f'inventory:{resource.key}-list'),
            'can_add': self.request.user.has_perm(resource.permission('add')),
            'can_delete': self.request.user.has_perm(resource.permission('delete')),
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in resource.columns],
        })
        can_change = self.request.user.has_perm(resource.permission('change'))
        if can_change or context['can_delete']:
            context['columns'].append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
        if can_change:
            context['edit_url_template'] = reverse(f'inventory_pages:edit-{resource.key}', args=[0])
        if context['can_delete']:
            context['delete_url_template'] = reverse(f'inventory:{resource.key}-detail', args=[0])
        if resource.key == 'currency':
            for column in context['columns']:
                if column['key'] == 'exchange_rate':
                    column['label'] = f'Rate ({settings.INVENTORY_BASE_CURRENCY} per unit)'
        return context


class InventoryListPageView(InventoryPageView):
    template_name = 'inventory/list.html'


class InventoryAddPageView(InventoryPageView):
    template_name = 'inventory/add.html'
    mode = 'add'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'fields_template': f'inventory/fields/{self.resource.key}.html',
            'is_product': self.resource_key == 'product',
            'form_heading': f'Add {self.resource.singular}',
            'save_label': f'Add {self.resource.singular}',
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'options_api_url': reverse('inventory:product-options') if self.resource_key == 'product' else '',
        })
        return context


class InventoryEditPageView(InventoryAddPageView):
    mode = 'edit'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_label': f'Edit {self.resource.singular}', 'form_heading': f'Edit {self.resource.singular}',
            'save_label': 'Save changes', 'form_method': 'PATCH',
            'create_api_url': reverse(f'inventory:{self.resource.key}-update', args=[self.kwargs['pk']]),
            'detail_api_url': reverse(f'inventory:{self.resource.key}-detail', args=[self.kwargs['pk']]),
            'editing_currency': self.resource_key == 'currency',
            'lock_code': self.resource_key in ('product-type', 'status', 'currency'),
        })
        if self.resource_key == 'product':
            context['options_api_url'] = reverse('inventory:product-edit-options', args=[self.kwargs['pk']])
        return context
