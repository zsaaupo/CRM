from account.permissions import has_role_permissions
from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from .scope import WarehouseScopePageMixin, all_warehouse_access, warehouse_scope


class WareHousePageView(WarehouseScopePageMixin, ProtectedAccountPageView):
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
        can_change = has_role_permissions(user, ('warehouse.change_warehouse',))
        can_delete = has_role_permissions(user, ('warehouse.delete_warehouse',))
        if can_change or can_delete:
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
            'can_add': has_role_permissions(user, ('warehouse.add_warehouse',)) and all_warehouse_access(warehouse_scope(user)),
            'can_delete': can_delete, 'columns': columns,
        })
        if can_change:
            context['edit_url_template'] = reverse('warehouse_pages:edit-warehouse', args=[0])
        if can_delete:
            context['delete_url_template'] = reverse('warehouse:warehouse-detail', args=[0])
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
    requires_all_warehouses = True
    template_name = 'warehouse/add.html'
    mode = 'add'


class WareHouseEditPageView(WareHousePageView):
    template_name = 'warehouse/add.html'
    mode = 'edit'


class StockListPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'inventory/list.html'
    permission_required = ('warehouse.view_stock',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'warehouses', 'page_mode': 'stock-list', 'page_label': 'Stock list',
            'section_label': 'Warehouses', 'resource': {'singular': 'stock', 'plural': 'stock'},
            'list_api_url': reverse('warehouse:stock-list'),
            'view_url_template': reverse('warehouse_pages:stock-detail', args=[0]),
            'list_description': 'Current quantities by warehouse and product.',
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in [
                ('warehouse_name', 'Warehouse', 'text'), ('product_sku', 'SKU', 'text'),
                ('product_name', 'Product', 'text'), ('quantity', 'Quantity', 'text'),
                ('unit_code', 'Unit', 'text'), ('actions', 'Actions', 'edit'),
            ]],
        })
        return context


class StockDetailPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'warehouse/stock_detail.html'
    permission_required = ('warehouse.view_stock',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'warehouses', 'page_mode': 'stock-detail', 'page_label': 'Stock details',
            'list_url': reverse('warehouse_pages:stock-list'),
            'detail_api_url': reverse('warehouse:stock-detail', args=[self.kwargs['pk']]),
        })
        return context
