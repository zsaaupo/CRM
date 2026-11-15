from account.permissions import has_role_permissions
from django.conf import settings
from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from warehouse.scope import WarehouseScopePageMixin


PURCHASE_CREATE_PERMISSIONS = (
    'purchase.view_purchaseorder', 'purchase.add_purchaseorder', 'purchase.add_purchaseorderitem',
)
PURCHASE_EDIT_PERMISSIONS = (
    'purchase.view_purchaseorder', 'purchase.change_purchaseorder', 'purchase.view_purchaseorderitem',
    'purchase.add_purchaseorderitem', 'purchase.change_purchaseorderitem', 'purchase.delete_purchaseorderitem',
)


class PurchaseListPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'inventory/list.html'
    permission_required = ('purchase.view_purchaseorder',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'purchase', 'page_mode': 'list', 'page_label': 'Purchase list',
            'section_label': 'Purchase', 'resource': {'singular': 'purchase', 'plural': 'purchases'},
            'list_url': reverse('purchase_pages:purchase-list'),
            'add_url': reverse('purchase_pages:create-purchase'),
            'list_api_url': reverse('purchase:purchase-order-list-view'),
            'view_url_template': reverse('purchase_pages:purchase-detail', args=[0]),
            'can_add': has_role_permissions(self.request.user, PURCHASE_CREATE_PERMISSIONS),
            'can_delete': has_role_permissions(self.request.user, ('purchase.delete_purchaseorder', 'purchase.delete_purchaseorderitem')),
            'columns': [
                {'key': key, 'label': label, 'type': kind}
                for key, label, kind in [
                    ('order_number', 'Order number', 'text'), ('supplier_name', 'Supplier', 'text'),
                    ('order_date', 'Order date', 'text'), ('expected_date', 'Expected date', 'text'),
                    ('status', 'Status', 'status'), ('grand_total', 'Total', 'price'),
                    ('actions', 'Actions', 'edit'),
                ]
            ],
        })
        if has_role_permissions(self.request.user, PURCHASE_EDIT_PERMISSIONS):
            context['edit_url_template'] = reverse('purchase_pages:edit-purchase', args=[0])
            context['edit_disabled_field'] = 'is_received'
        if context['can_delete']:
            context['delete_url_template'] = reverse('purchase:purchase-order-detail', args=[0])
            context['delete_disabled_field'] = 'is_received'
        if has_role_permissions(self.request.user, ('purchase.change_purchaseorder',)):
            context['receive_api_url'] = reverse('purchase:purchase-order-receive')
        return context


class PurchaseDetailPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'purchase/detail.html'
    permission_required = ('purchase.view_purchaseorder',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'purchase', 'page_mode': 'detail', 'page_label': 'Purchase details',
            'list_url': reverse('purchase_pages:purchase-list'),
            'detail_api_url': reverse('purchase:purchase-order-detail', args=[self.kwargs['pk']]),
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'can_view_items': has_role_permissions(self.request.user, ('purchase.view_purchaseorderitem',)),
        })
        if context['can_view_items']:
            context['items_api_url'] = (reverse('purchase:purchase-order-item-list-view')
                                        + f'?purchase_order={self.kwargs["pk"]}')
        if has_role_permissions(self.request.user, ('inventory.view_product',)):
            context['product_url_template'] = reverse('inventory_pages:product-detail', args=[0])
        return context


class PurchaseCreatePageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    template_name = 'purchase/create.html'
    permission_required = PURCHASE_CREATE_PERMISSIONS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'purchase', 'page_mode': 'add', 'page_label': 'Create purchase',
            'list_url': reverse('purchase_pages:purchase-list'),
            'create_api_url': reverse('purchase:purchase-order-list'),
            'options_api_url': reverse('purchase:purchase-options'),
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
        })
        return context


class PurchaseEditPageView(PurchaseCreatePageView):
    permission_required = PURCHASE_EDIT_PERMISSIONS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs['pk']
        context.update({
            'page_mode': 'edit', 'page_label': 'Edit purchase', 'form_heading': 'Edit purchase',
            'form_method': 'PATCH', 'save_label': 'Save changes',
            'create_api_url': reverse('purchase:purchase-order-update', args=[pk]),
            'detail_api_url': reverse('purchase:purchase-order-detail', args=[pk]),
            'items_api_url': reverse('purchase:purchase-order-item-list-view') + f'?purchase_order={pk}',
            'options_api_url': reverse('purchase:purchase-edit-options'),
        })
        return context
