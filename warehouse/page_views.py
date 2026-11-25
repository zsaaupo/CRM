from account.permissions import has_role_permissions
from django.urls import reverse
from django.utils import timezone

from account.page_views import ProtectedAccountPageView
from .activity import available_reports
from .models import WareHouse
from .scope import WarehouseScopePageMixin, all_warehouse_access, has_warehouse_scope, scoped_queryset, warehouse_scope


class WarehouseReportPageView(ProtectedAccountPageView):
    template_name = 'warehouse/report.html'

    def has_permission(self):
        return has_warehouse_scope(self.request.user) and bool(available_reports(warehouse_scope(self.request.user)))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = warehouse_scope(self.request.user)
        reports = {}
        for key, (label, _, columns) in available_reports(scope).items():
            reports[key] = {
                'label': label, 'url': reverse(f'warehouse:report-{key}', args=[0]),
                'columns': [dict(key=col[0], label=col[1], type=col[2] if len(col) > 2 else 'text') for col in columns],
            }
        context.update(page_section='warehouses', page_mode='report', page_label='Warehouse report', reports=reports,
                       warehouses=scoped_queryset(WareHouse.objects.order_by('name', 'pk'), scope, 'pk'),
                       choose_warehouse=all_warehouse_access(scope))
        return context


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
                ('product_name', 'Product', 'text'), ('quantity', 'Current quantity', 'text'),
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


class TransferListPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'inventory/list.html'
    permission_required = ('warehouse.view_warehousetransfer',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        can_write = all_warehouse_access(warehouse_scope(user))
        can_change = can_write and has_role_permissions(user, ('warehouse.change_warehousetransfer',))
        can_delete = can_write and has_role_permissions(user, ('warehouse.delete_warehousetransfer',))
        context.update({
            'page_section': 'warehouses', 'page_mode': 'transfer-list', 'page_label': 'Warehouse transfers',
            'section_label': 'Warehouses', 'resource': {'singular': 'transfer', 'plural': 'transfers'},
            'list_url': reverse('warehouse_pages:transfer-list'),
            'add_url': reverse('warehouse_pages:add-transfer'),
            'list_api_url': reverse('warehouse:transfer-list-view'),
            'view_url_template': reverse('warehouse_pages:transfer-detail', args=[0]),
            'can_add': can_write and has_role_permissions(user, ('warehouse.add_warehousetransfer',)),
            'can_delete': can_delete,
            'list_description': 'Move products between warehouses. Completed transfers are read-only.',
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in [
                ('transfer_number', 'Transfer number', 'text'), ('transfer_date', 'Date', 'text'),
                ('source_warehouse_name', 'From', 'text'), ('destination_warehouse_name', 'To', 'text'),
                ('product_summary', 'Products', 'text'), ('item_count', 'Lines', 'text'),
                ('is_completed', 'Completed', 'boolean'), ('actions', 'Actions', 'edit'),
            ]],
        })
        if can_change:
            context.update({
                'edit_url_template': reverse('warehouse_pages:edit-transfer', args=[0]),
                'edit_disabled_field': 'is_completed',
                'complete_api_url': reverse('warehouse:transfer-complete'),
                'complete_id_field': 'transfer_id', 'complete_result_key': 'warehouse_transfer',
                'complete_label': 'transfer',
            })
        if can_delete:
            context['delete_url_template'] = reverse('warehouse:transfer-detail', args=[0])
            context['delete_disabled_field'] = 'is_completed'
        return context


class TransferAddPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    requires_all_warehouses = True
    template_name = 'warehouse/transfer_form.html'
    permission_required = ('warehouse.view_warehousetransfer', 'warehouse.add_warehousetransfer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'warehouses', 'page_mode': 'transfer-add', 'page_label': 'Add warehouse transfer',
            'section_label': 'Warehouses', 'resource': {'singular': 'transfer', 'plural': 'transfers'},
            'form_heading': 'Add warehouse transfer', 'save_label': 'Create transfer',
            'form_description': 'Choose the source, destination and products. Stock moves when you complete the transfer.',
            'fields_template': 'warehouse/transfer_fields.html',
            'list_url': reverse('warehouse_pages:transfer-list'),
            'create_api_url': reverse('warehouse:transfer-list'),
            'options_api_url': reverse('warehouse:transfer-options'),
            'today': timezone.localdate(),
        })
        return context


class TransferEditPageView(TransferAddPageView):
    permission_required = ('warehouse.view_warehousetransfer', 'warehouse.change_warehousetransfer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_mode': 'transfer-edit', 'page_label': 'Edit warehouse transfer',
            'form_heading': 'Edit warehouse transfer', 'save_label': 'Save changes', 'form_method': 'PATCH',
            'create_api_url': reverse('warehouse:transfer-update', args=[self.kwargs['pk']]),
            'detail_api_url': reverse('warehouse:transfer-detail', args=[self.kwargs['pk']]),
            'options_api_url': reverse('warehouse:transfer-edit-options'),
            'readonly_field': 'is_completed', 'readonly_message': 'Completed transfers are read-only. Return to the transfer list.',
        })
        return context


class TransferDetailPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'warehouse/transfer_detail.html'
    permission_required = ('warehouse.view_warehousetransfer',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'warehouses', 'page_mode': 'transfer-detail', 'page_label': 'Transfer details',
            'list_url': reverse('warehouse_pages:transfer-list'),
            'detail_api_url': reverse('warehouse:transfer-detail', args=[self.kwargs['pk']]),
            'dispatch_url': reverse('warehouse:transfer-dispatch', args=[self.kwargs['pk']]),
            'receive_url': reverse('warehouse:transfer-receive', args=[self.kwargs['pk']]),
        })
        return context


class StockAdjustmentPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'warehouse/adjustments.html'
    permission_required = ('warehouse.view_stockadjustment',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'warehouses',
            'page_mode': 'adjustment-list',
            'page_label': 'Stock adjustments',
            'api_url': reverse('warehouse:adjustment-list'),
        })
        return context


class StockCountPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'warehouse/stock_counts.html'
    permission_required = ('warehouse.manage_stock_count',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'warehouses',
            'page_mode': 'stock-count-list',
            'page_label': 'Physical stock counts',
            'api_url': reverse('warehouse:stock-count-list'),
        })
        return context
