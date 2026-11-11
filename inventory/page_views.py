from django.core.exceptions import ImproperlyConfigured
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
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in resource.columns],
        })
        return context


class InventoryListPageView(InventoryPageView):
    template_name = 'inventory/list.html'


class InventoryAddPageView(InventoryPageView):
    template_name = 'inventory/add.html'
    mode = 'add'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = self.resource.form()
        if self.resource_key == 'product':
            groups = [
                ('Product details', 'Identify the item and choose its catalog references.',
                 ['sku', 'name', 'barcode', 'product_type', 'status', 'category', 'brand', 'unit', 'variant_of']),
                ('Description and image', 'Add the details that help people recognize this product.',
                 ['description', 'image', 'manufacturer', 'manufacturer_part_number', 'attributes']),
                ('Pricing', 'Set catalog prices and tax defaults in the selected currency.',
                 ['currency', 'cost_price', 'sale_price', 'minimum_sale_price', 'Whole_sale_price', 'product_tax', 'is_returnable']),
            ]
        else:
            groups = [(f'{self.resource.singular.capitalize()} details',
                       'Enter a clear name and the details below.', list(form.fields))]
        missing = []
        for name, field in form.fields.items():
            if field.required and hasattr(field, 'queryset') and not field.queryset.exists():
                missing.append(field.label.lower())
        context.update({
            'form': form,
            'sections': [{'title': title, 'description': description, 'fields': [form[name] for name in names]}
                         for title, description, names in groups],
            'is_product': self.resource_key == 'product',
            'missing_references': missing,
        })
        return context
