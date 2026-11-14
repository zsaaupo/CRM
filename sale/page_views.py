from django.conf import settings
from django.urls import reverse

from account.page_views import ProtectedAccountPageView


SALES_CREATE_PERMISSIONS = (
    'sale.view_salesorder', 'sale.add_salesorder', 'sale.add_salesorderitem',
)
SALES_EDIT_PERMISSIONS = (
    'sale.view_salesorder', 'sale.change_salesorder', 'sale.view_salesorderitem',
    'sale.add_salesorderitem', 'sale.change_salesorderitem', 'sale.delete_salesorderitem',
)


class SalesListPageView(ProtectedAccountPageView):
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
            'can_add': self.request.user.has_perms(SALES_CREATE_PERMISSIONS),
            'can_delete': self.request.user.has_perms(('sale.delete_salesorder', 'sale.delete_salesorderitem')),
            'list_description': 'Customer orders, dates and totals.',
            'columns': [
                {'key': key, 'label': label, 'type': kind}
                for key, label, kind in [
                    ('order_number', 'Order number', 'text'), ('customer_name', 'Customer', 'text'),
                    ('order_date', 'Order date', 'text'), ('expected_delivery_date', 'Expected date', 'text'),
                    ('status', 'Status', 'status'), ('grand_total', 'Total', 'price'),
                    ('actions', 'Actions', 'edit'),
                ]
            ],
        })
        if self.request.user.has_perms(SALES_EDIT_PERMISSIONS):
            context['edit_url_template'] = reverse('sale_pages:edit-sale', args=[0])
        if context['can_delete']:
            context['delete_url_template'] = reverse('sale:sales-order-detail', args=[0])
        return context


class SalesDetailPageView(ProtectedAccountPageView):
    template_name = 'sale/detail.html'
    permission_required = ('sale.view_salesorder',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'sale', 'page_mode': 'detail', 'page_label': 'Sales details',
            'list_url': reverse('sale_pages:sale-list'),
            'detail_api_url': reverse('sale:sales-order-detail', args=[self.kwargs['pk']]),
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'can_view_items': self.request.user.has_perm('sale.view_salesorderitem'),
        })
        if context['can_view_items']:
            context['items_api_url'] = (reverse('sale:sales-order-item-list-view')
                                        + f'?sales_order={self.kwargs["pk"]}')
        if self.request.user.has_perm('inventory.view_product'):
            context['product_url_template'] = reverse('inventory_pages:product-detail', args=[0])
        if self.request.user.has_perms(SALES_EDIT_PERMISSIONS):
            context['edit_url'] = reverse('sale_pages:edit-sale', args=[self.kwargs['pk']])
        return context


class SalesCreatePageView(ProtectedAccountPageView):
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
