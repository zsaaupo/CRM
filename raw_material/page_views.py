from django.conf import settings
from django.shortcuts import get_object_or_404
from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from warehouse.scope import WarehouseScopePageMixin, all_warehouse_access, scoped_queryset, warehouse_scope

from .models import RawMaterial


RAW_MATERIAL_CREATE_PERMISSIONS = ('raw_material.view_rawmaterial', 'raw_material.add_rawmaterial')
RAW_MATERIAL_EDIT_PERMISSIONS = ('raw_material.view_rawmaterial', 'raw_material.change_rawmaterial')


class RawMaterialListPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'inventory/list.html'
    permission_required = ('raw_material.view_rawmaterial',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        can_change = has_role_permissions(user, RAW_MATERIAL_EDIT_PERMISSIONS)
        can_delete = has_role_permissions(user, ('raw_material.delete_rawmaterial',))
        columns = [
            {'key': key, 'label': label, 'type': kind}
            for key, label, kind in (
                ('name', 'Material', 'identity'), ('sku', 'SKU', 'text'), ('warehouse_name', 'Warehouse', 'text'),
                ('unit_code', 'Unit', 'text'), ('quantity', 'On hand', 'text'),
                ('reorder_level', 'Reorder level', 'text'), ('low_stock', 'Low stock', 'boolean'),
                ('status', 'Status', 'status'), ('actions', 'Actions', 'edit'),
            )
        ]
        context.update({
            'resource': {'singular': 'raw material', 'plural': 'raw materials'},
            'page_section': 'raw_material', 'section_label': 'Raw Material', 'page_mode': 'list',
            'page_label': 'Raw material list',
            'list_url': reverse('raw_material_pages:material-list'),
            'add_url': reverse('raw_material_pages:add-material'),
            'list_api_url': reverse('raw_material:material-list-view'),
            'view_url_template': reverse('raw_material_pages:material-detail', args=[0]),
            'can_add': has_role_permissions(user, RAW_MATERIAL_CREATE_PERMISSIONS),
            'can_delete': can_delete,
            'columns': columns,
            'hide_list_hint': True,
        })
        if can_change:
            context['edit_url_template'] = reverse('raw_material_pages:edit-material', args=[0])
        if can_delete:
            context['delete_url_template'] = reverse('raw_material:material-detail', args=[0])
        return context


class RawMaterialDetailPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'raw_material/material_detail.html'
    permission_required = ('raw_material.view_rawmaterial',)

    def get_context_data(self, **kwargs):
        user = self.request.user
        records = scoped_queryset(RawMaterial.objects.all(), warehouse_scope(user))
        record = get_object_or_404(records, pk=self.kwargs['pk'])
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'raw_material', 'page_mode': 'detail', 'page_label': 'Raw material details',
            'list_url': reverse('raw_material_pages:material-list'),
            'detail_api_url': reverse('raw_material:material-detail', args=[record.pk]),
        })
        if has_role_permissions(user, RAW_MATERIAL_EDIT_PERMISSIONS):
            context['edit_url'] = reverse('raw_material_pages:edit-material', args=[record.pk])
        return context


class RawMaterialCreatePageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'inventory/add.html'
    permission_required = RAW_MATERIAL_CREATE_PERMISSIONS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = warehouse_scope(self.request.user)
        warehouse_locked = not all_warehouse_access(scope)
        context.update({
            'page_section': 'raw_material', 'section_label': 'Raw Material', 'page_mode': 'add',
            'resource': {'singular': 'raw material', 'plural': 'raw materials'},
            'page_label': 'Add raw material', 'form_heading': 'Add raw material', 'save_label': 'Add raw material',
            'list_url': reverse('raw_material_pages:material-list'),
            'fields_template': 'raw_material/fields.html', 'hide_catalog_hint': True,
            'create_api_url': reverse('raw_material:material-list'),
            'options_api_url': reverse('raw_material:material-options'),
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'warehouse_locked': warehouse_locked,
            'warehouse_default': scope.warehouse_id if warehouse_locked else '',
        })
        return context


class RawMaterialEditPageView(RawMaterialCreatePageView):
    permission_required = RAW_MATERIAL_EDIT_PERMISSIONS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = warehouse_scope(self.request.user)
        record = get_object_or_404(scoped_queryset(RawMaterial.objects.all(), scope), pk=self.kwargs['pk'])
        context.update({
            'page_mode': 'edit', 'page_label': 'Edit raw material', 'form_heading': 'Edit raw material',
            'save_label': 'Save changes', 'form_method': 'PATCH',
            'create_api_url': reverse('raw_material:material-update', args=[record.pk]),
            'detail_api_url': reverse('raw_material:material-detail', args=[record.pk]),
            'options_api_url': reverse('raw_material:material-edit-options', args=[record.pk]),
            'warehouse_default': record.warehouse_id,
        })
        if not context['warehouse_locked']:
            context['warehouse_hint'] = 'Use a warehouse transfer to move existing stock.'
        return context
