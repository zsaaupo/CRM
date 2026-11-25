from account.permissions import has_role_permissions
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from account.page_views import ProtectedAccountPageView
from warehouse.scope import WarehouseScopePageMixin, warehouse_scope

from .models import PurchaseReturn
from .returns import RETURN_CREATE, RETURN_EDIT, RETURN_VIEW, scoped_returns


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
                    ('status', 'Status', 'status'), ('payment_status', 'Payment status', 'text'),
                    ('grand_total', 'Total', 'price'),
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
        if has_role_permissions(self.request.user, RETURN_CREATE):
            context['return_url'] = f"{reverse('purchase_pages:create-return')}?purchase_order={self.kwargs['pk']}"
        if has_role_permissions(self.request.user, ('purchase.view_purchaseorder', 'accounting.view_financialaccount', 'accounting.view_purchasepayment')):
            context['payments_url'] = reverse('purchase_pages:purchase-payment-list', args=[self.kwargs['pk']])
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
            'enable_in_house_production': has_role_permissions(
                self.request.user, ('manufacturing.view_productionorder',)
            ),
            'manufacturing_url': reverse('manufacturing_pages:home'),
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


class PurchaseReturnPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    permission_required = RETURN_VIEW

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            page_section='purchase', purchase_area='returns', section_label='Purchase',
            list_url=reverse('purchase_pages:return-list'), return_type='purchase',
            return_title='Purchase return', return_list_label='purchase returns',
            return_order_label='Original purchase order', return_order_field='purchase_order',
            return_order_number_field='purchase_order_number', return_original_label='Original purchase',
            return_party_field='supplier_name', return_party_label='Supplier', return_quantity_label='Received',
            return_complete_label='Complete return',
        )
        return context

    def get_record(self):
        records = scoped_returns(PurchaseReturn.objects.all(), warehouse_scope(self.request.user))
        return get_object_or_404(records, pk=self.kwargs['pk'])


class PurchaseReturnListPageView(PurchaseReturnPageView):
    template_name = 'inventory/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        can_delete = has_role_permissions(user, ('purchase.delete_purchasereturn', 'purchase.delete_purchasereturnitem'))
        context.update({
            'page_mode': 'list', 'page_label': 'Purchase returns', 'resource': {'singular': 'purchase return', 'plural': 'purchase returns'},
            'list_description': 'Returns against received purchases.', 'list_api_url': reverse('purchase:purchase-return-list-view'),
            'add_url': reverse('purchase_pages:create-return'), 'add_label': 'Create return',
            'can_add': has_role_permissions(user, RETURN_CREATE), 'can_delete': can_delete,
            'view_url_template': reverse('purchase_pages:return-detail', args=[0]),
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in (
                ('return_number', 'Return number', 'text'), ('purchase_order_number', 'Purchase order', 'text'),
                ('supplier_name', 'Supplier', 'text'), ('warehouse_name', 'Warehouse', 'text'),
                ('return_date', 'Return date', 'text'), ('is_completed', 'Completed', 'boolean'), ('actions', 'Actions', 'edit'),
            )],
        })
        if has_role_permissions(user, RETURN_EDIT):
            context.update(edit_url_template=reverse('purchase_pages:edit-return', args=[0]), edit_disabled_field='is_completed')
        if can_delete:
            context.update(delete_url_template=reverse('purchase:purchase-return-detail', args=[0]), delete_disabled_field='is_completed')
        if has_role_permissions(user, ('purchase.change_purchasereturn',)):
            context.update(complete_api_url=reverse('purchase:purchase-return-complete'), complete_id_field='return_id',
                           complete_result_key='purchase_return', complete_label='return')
        return context


class PurchaseReturnCreatePageView(PurchaseReturnPageView):
    template_name = 'returns/form.html'
    permission_required = RETURN_CREATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_mode='add', form_heading='Create purchase return',
                       create_api_url=reverse('purchase:purchase-return-list'), options_api_url=reverse('purchase:purchase-return-options'),
                       selected_order=self.request.GET.get('purchase_order', ''),
                       today=timezone.localdate().isoformat())
        return context


class PurchaseReturnEditPageView(PurchaseReturnCreatePageView):
    permission_required = RETURN_EDIT

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.get_record().pk
        context.update(page_mode='edit', form_heading='Edit purchase return', form_method='PATCH',
                       create_api_url=reverse('purchase:purchase-return-update', args=[pk]),
                       detail_api_url=reverse('purchase:purchase-return-detail', args=[pk]),
                       options_api_url=reverse('purchase:purchase-return-edit-options', args=[pk]))
        return context


class PurchaseReturnDetailPageView(PurchaseReturnPageView):
    template_name = 'purchase/return_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        record = self.get_record()
        user = self.request.user
        context.update(page_mode='detail', detail_api_url=reverse('purchase:purchase-return-detail', args=[record.pk]),
                       can_view_items=has_role_permissions(user, ('purchase.view_purchasereturnitem',)))
        if has_role_permissions(user, ('purchase.view_purchaseorder',)):
            context['order_url'] = reverse('purchase_pages:purchase-detail', args=[record.purchase_order_id])
        if has_role_permissions(user, RETURN_EDIT):
            context['edit_url'] = reverse('purchase_pages:edit-return', args=[record.pk])
        if has_role_permissions(user, ('purchase.change_purchasereturn',)):
            context['complete_api_url'] = reverse('purchase:purchase-return-complete')
        return context
