from account.permissions import has_role_permissions
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from account.page_views import ProtectedAccountPageView
from warehouse.scope import WarehouseScopePageMixin, warehouse_scope

from .models import SalesReturn
from .returns import RETURN_CREATE, RETURN_EDIT, RETURN_VIEW, scoped_returns


SALES_CREATE_PERMISSIONS = (
    'sale.view_salesorder', 'sale.add_salesorder', 'sale.add_salesorderitem',
)
SALES_EDIT_PERMISSIONS = (
    'sale.view_salesorder', 'sale.change_salesorder', 'sale.view_salesorderitem',
    'sale.add_salesorderitem', 'sale.change_salesorderitem', 'sale.delete_salesorderitem',
)


class SalesListPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'inventory/list.html'
    permission_required = ('sale.view_salesorder',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'sale', 'page_mode': 'list', 'page_label': 'Sales list',
            'section_label': 'Sales', 'resource': {'singular': 'sale', 'plural': 'sales'},
            'list_url': reverse('sale_pages:sale-list'),
            'add_url': reverse('sale_pages:create-sale'),
            'list_api_url': reverse('sale:sales-order-list-view'),
            'view_url_template': reverse('sale_pages:sale-detail', args=[0]),
            'can_add': has_role_permissions(self.request.user, SALES_CREATE_PERMISSIONS),
            'can_delete': has_role_permissions(self.request.user, ('sale.delete_salesorder', 'sale.delete_salesorderitem')),
            'list_description': 'Customer orders, dates and totals.',
            'columns': [
                {'key': key, 'label': label, 'type': kind}
                for key, label, kind in [
                    ('order_number', 'Order number', 'text'), ('customer_name', 'Customer', 'text'),
                    ('order_date', 'Order date', 'text'), ('expected_delivery_date', 'Expected date', 'text'),
                    ('status', 'Status', 'status'), ('grand_total', 'Total', 'price'),
                    ('is_completed', 'Completed', 'boolean'),
                    ('actions', 'Actions', 'edit'),
                ]
            ],
        })
        if has_role_permissions(self.request.user, SALES_EDIT_PERMISSIONS):
            context['edit_url_template'] = reverse('sale_pages:edit-sale', args=[0])
            context['edit_disabled_field'] = 'is_completed'
        if context['can_delete']:
            context['delete_url_template'] = reverse('sale:sales-order-detail', args=[0])
            context['delete_disabled_field'] = 'is_completed'
        if has_role_permissions(self.request.user, ('sale.change_salesorder',)):
            context['complete_api_url'] = reverse('sale:sales-order-complete')
        return context


class SalesDetailPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'sale/detail.html'
    permission_required = ('sale.view_salesorder',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'sale', 'page_mode': 'detail', 'page_label': 'Sales details',
            'list_url': reverse('sale_pages:sale-list'),
            'detail_api_url': reverse('sale:sales-order-detail', args=[self.kwargs['pk']]),
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'can_view_items': has_role_permissions(self.request.user, ('sale.view_salesorderitem',)),
        })
        if context['can_view_items']:
            context['items_api_url'] = (reverse('sale:sales-order-item-list-view')
                                        + f'?sales_order={self.kwargs["pk"]}')
        if has_role_permissions(self.request.user, ('inventory.view_product',)):
            context['product_url_template'] = reverse('inventory_pages:product-detail', args=[0])
        if has_role_permissions(self.request.user, SALES_EDIT_PERMISSIONS):
            context['edit_url'] = reverse('sale_pages:edit-sale', args=[self.kwargs['pk']])
        if has_role_permissions(self.request.user, RETURN_CREATE):
            context['return_url'] = f"{reverse('sale_pages:create-return')}?sales_order={self.kwargs['pk']}"
        return context


class SalesCreatePageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'sale/create.html'
    permission_required = SALES_CREATE_PERMISSIONS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'sale', 'page_mode': 'add', 'page_label': 'Create sale',
            'list_url': reverse('sale_pages:sale-list'),
            'create_api_url': reverse('sale:sales-order-list'),
            'options_api_url': reverse('sale:sale-options'),
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
        })
        return context


class SalesEditPageView(SalesCreatePageView):
    permission_required = SALES_EDIT_PERMISSIONS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs['pk']
        context.update({
            'page_mode': 'edit', 'page_label': 'Edit sale', 'form_heading': 'Edit sale',
            'form_method': 'PATCH', 'save_label': 'Save changes',
            'create_api_url': reverse('sale:sales-order-update', args=[pk]),
            'detail_api_url': reverse('sale:sales-order-detail', args=[pk]),
            'items_api_url': reverse('sale:sales-order-item-list-view') + f'?sales_order={pk}',
            'options_api_url': reverse('sale:sales-edit-options'),
        })
        return context


class SalesReturnPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    permission_required = RETURN_VIEW

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_section='sale', sale_area='returns', section_label='Sales',
                       list_url=reverse('sale_pages:return-list'))
        return context

    def get_record(self):
        records = scoped_returns(SalesReturn.objects.all(), warehouse_scope(self.request.user))
        return get_object_or_404(records, pk=self.kwargs['pk'])


class SalesReturnListPageView(SalesReturnPageView):
    template_name = 'inventory/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        can_delete = has_role_permissions(user, ('sale.delete_salesreturn', 'sale.delete_salesreturnitem'))
        context.update({
            'page_mode': 'list', 'page_label': 'Sales returns', 'resource': {'singular': 'sales return', 'plural': 'sales returns'},
            'list_description': 'Returns against completed sales.', 'list_api_url': reverse('sale:sales-return-list-view'),
            'add_url': reverse('sale_pages:create-return'), 'add_label': 'Create return',
            'can_add': has_role_permissions(user, RETURN_CREATE), 'can_delete': can_delete,
            'view_url_template': reverse('sale_pages:return-detail', args=[0]),
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in (
                ('return_number', 'Return number', 'text'), ('sales_order_number', 'Sales order', 'text'),
                ('customer_name', 'Customer', 'text'), ('warehouse_name', 'Warehouse', 'text'),
                ('return_date', 'Return date', 'text'), ('is_completed', 'Completed', 'boolean'), ('actions', 'Actions', 'edit'),
            )],
        })
        if has_role_permissions(user, RETURN_EDIT):
            context.update(edit_url_template=reverse('sale_pages:edit-return', args=[0]), edit_disabled_field='is_completed')
        if can_delete:
            context.update(delete_url_template=reverse('sale:sales-return-detail', args=[0]), delete_disabled_field='is_completed')
        if has_role_permissions(user, ('sale.change_salesreturn',)):
            context.update(complete_api_url=reverse('sale:sales-return-complete'), complete_id_field='return_id',
                           complete_result_key='sales_return', complete_label='return')
        return context


class SalesReturnCreatePageView(SalesReturnPageView):
    template_name = 'sale/return_form.html'
    permission_required = RETURN_CREATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_mode='add', form_heading='Create sales return',
                       create_api_url=reverse('sale:sales-return-list'), options_api_url=reverse('sale:sales-return-options'),
                       selected_order=self.request.GET.get('sales_order', ''),
                       today=timezone.localdate().isoformat())
        return context


class SalesReturnEditPageView(SalesReturnCreatePageView):
    permission_required = RETURN_EDIT

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.get_record().pk
        context.update(page_mode='edit', form_heading='Edit sales return', form_method='PATCH',
                       create_api_url=reverse('sale:sales-return-update', args=[pk]),
                       detail_api_url=reverse('sale:sales-return-detail', args=[pk]),
                       options_api_url=reverse('sale:sales-return-edit-options', args=[pk]))
        return context


class SalesReturnDetailPageView(SalesReturnPageView):
    template_name = 'sale/return_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        record = self.get_record()
        user = self.request.user
        context.update(page_mode='detail', detail_api_url=reverse('sale:sales-return-detail', args=[record.pk]),
                       can_view_items=has_role_permissions(user, ('sale.view_salesreturnitem',)))
        if has_role_permissions(user, ('sale.view_salesorder',)):
            context['order_url'] = reverse('sale_pages:sale-detail', args=[record.sales_order_id])
        if has_role_permissions(user, RETURN_EDIT):
            context['edit_url'] = reverse('sale_pages:edit-return', args=[record.pk])
        if has_role_permissions(user, ('sale.change_salesreturn',)):
            context['complete_api_url'] = reverse('sale:sales-return-complete')
        return context
