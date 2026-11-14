from django.conf import settings
from django.urls import reverse

from account.page_views import ProtectedAccountPageView


PURCHASE_CREATE_PERMISSIONS = (
    'purchase.view_purchaseorder', 'purchase.add_purchaseorder', 'purchase.add_purchaseorderitem',
)


class PurchaseListPageView(ProtectedAccountPageView):
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
            'can_add': self.request.user.has_perms(PURCHASE_CREATE_PERMISSIONS),
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
        return context


class PurchaseDetailPageView(ProtectedAccountPageView):
    template_name = 'purchase/detail.html'
    permission_required = ('purchase.view_purchaseorder',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'purchase', 'page_mode': 'detail', 'page_label': 'Purchase details',
            'list_url': reverse('purchase_pages:purchase-list'),
            'detail_api_url': reverse('purchase:purchase-order-detail', args=[self.kwargs['pk']]),
            'base_currency': settings.INVENTORY_BASE_CURRENCY,
            'can_view_items': self.request.user.has_perm('purchase.view_purchaseorderitem'),
        })
        if context['can_view_items']:
            context['items_api_url'] = (reverse('purchase:purchase-order-item-list-view')
                                        + f'?purchase_order={self.kwargs["pk"]}')
        if self.request.user.has_perm('inventory.view_product'):
            context['product_url_template'] = reverse('inventory_pages:product-detail', args=[0])
        return context


class PurchaseCreatePageView(ProtectedAccountPageView):
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
