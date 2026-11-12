from django.urls import reverse

from account.page_views import ProtectedAccountPageView


class WareHousePageView(ProtectedAccountPageView):
    mode = 'list'

    def get_permission_required(self):
        permissions = ['warehouse.view_warehouse']
        if self.mode == 'add':
            permissions.append('warehouse.add_warehouse')
        elif self.mode == 'edit':
            permissions.append('warehouse.change_warehouse')
        return permissions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        columns = [
            {'key': key, 'label': label, 'type': kind}
            for key, label, kind in [
                ('name', 'Name', 'text'), ('code', 'Code', 'text'),
                ('address', 'Address', 'text'), ('phone', 'Phone', 'text'),
                ('email', 'Email', 'text'), ('is_active', 'Status', 'status'),
            ]
        ]
        can_change = user.has_perm('warehouse.change_warehouse')
        can_delete = user.has_perm('warehouse.delete_warehouse')
        can_add_stock = user.has_perm('warehouse.add_stock')
        if can_change or can_delete or can_add_stock:
            columns.append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
        heading = 'Warehouse list' if self.mode == 'list' else f'{self.mode.capitalize()} warehouse'
        context.update({
            'resource': {'singular': 'warehouse', 'plural': 'warehouses'},
            'page_section': 'warehouses', 'section_label': 'Warehouses',
            'page_mode': self.mode, 'page_label': heading, 'form_heading': heading,
            'save_label': 'Save changes' if self.mode == 'edit' else 'Add warehouse',
            'list_url': reverse('warehouse_pages:warehouse-list'),
            'add_url': reverse('warehouse_pages:add-warehouse'),
            'list_api_url': reverse('warehouse:warehouse-list-view'),
            'create_api_url': reverse('warehouse:warehouse-list'),
            'fields_template': 'warehouse/fields.html',
            'can_add': user.has_perm('warehouse.add_warehouse'),
            'can_delete': can_delete, 'columns': columns,
        })
        if can_change:
            context['edit_url_template'] = reverse('warehouse_pages:edit-warehouse', args=[0])
        if can_delete:
            context['delete_url_template'] = reverse('warehouse:warehouse-detail', args=[0])
        if can_add_stock:
            context['stock_url_template'] = reverse('warehouse_pages:add-stock') + '?warehouse=__warehouse__'
        if self.mode == 'edit':
            context.update({
                'form_method': 'PATCH',
                'create_api_url': reverse('warehouse:warehouse-update', args=[self.kwargs['pk']]),
                'detail_api_url': reverse('warehouse:warehouse-detail', args=[self.kwargs['pk']]),
            })
        return context


class WareHouseListPageView(WareHousePageView):
    template_name = 'warehouse/list.html'


class WareHouseAddPageView(WareHousePageView):
    template_name = 'warehouse/add.html'
    mode = 'add'


class WareHouseEditPageView(WareHousePageView):
    template_name = 'warehouse/add.html'
    mode = 'edit'


class StockAddPageView(ProtectedAccountPageView):
    template_name = 'warehouse/stock_add.html'
    permission_required = ('warehouse.add_stock',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_view = self.request.user.has_perm('warehouse.view_warehouse')
        requested_warehouse = self.request.GET.get('warehouse', '')
        context.update({
            'page_section': 'warehouses', 'page_mode': 'add-stock', 'page_label': 'Add stock',
            'create_api_url': reverse('warehouse:stock-add'),
            'options_api_url': reverse('warehouse:stock-options'),
            'return_url': reverse('warehouse_pages:warehouse-list') if can_view else reverse('dashboard'),
            'return_label': 'Back to warehouses' if can_view else 'Back to dashboard',
            'selected_warehouse': requested_warehouse if requested_warehouse.isascii() and requested_warehouse.isdigit() and len(requested_warehouse) <= 20 else '',
        })
        return context
