from django.shortcuts import get_object_or_404
from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from warehouse.scope import WarehouseScopePageMixin, scoped_queryset, warehouse_scope

from .models import Chalan, PackingSlip
from .permissions import (
    CHALAN_CREATE, CHALAN_DELETE, CHALAN_EDIT, CHALAN_ITEM_VIEW, CHALAN_VIEW,
    DELIVERY_ASSIGN, DELIVERY_VIEW, PACKING_SLIP_GENERATE, PACKING_SLIP_PRINT, PACKING_SLIP_VIEW,
)


class ChalanPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    permission_required = CHALAN_VIEW

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_section='sale', sale_area='chalans', section_label='Sales',
                       list_url=reverse('sale_pages:chalan-list'))
        return context

    def get_record(self):
        records = scoped_queryset(Chalan.objects.select_related('delivery'), warehouse_scope(self.request.user))
        return get_object_or_404(records, pk=self.kwargs['pk'])


class ChalanListPageView(ChalanPageView):
    template_name = 'inventory/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context.update(
            page_mode='list', page_label='Chalan list', resource={'singular': 'Chalan', 'plural': 'Chalans'},
            hide_list_hint=True, list_description='Search by Chalan number, customer, or customer reference.',
            add_url=reverse('sale_pages:chalan-create'), add_label='Create Chalan',
            can_add=has_role_permissions(user, CHALAN_CREATE),
            list_api_url=reverse('sale:chalan-list-view'),
            view_url_template=reverse('sale_pages:chalan-detail', args=[0]),
            columns=[{'key': key, 'label': label, 'type': kind} for key, label, kind in (
                ('chalan_number', 'Chalan number', 'text'), ('customer_name', 'Customer', 'text'),
                ('warehouse_name', 'Warehouse', 'text'), ('chalan_date', 'Chalan date', 'text'),
                ('expected_delivery_date', 'Expected delivery', 'text'), ('grand_total', 'Total', 'price'),
                ('is_delivered', 'Delivered', 'boolean'), ('actions', 'Actions', 'edit'),
            )],
        )
        if has_role_permissions(user, CHALAN_EDIT):
            context.update(edit_url_template=reverse('sale_pages:chalan-edit', args=[0]), edit_disabled_field='is_delivered')
        if has_role_permissions(user, CHALAN_DELETE):
            context.update(can_delete=True, delete_url_template=reverse('sale:chalan-detail', args=[0]),
                           delete_disabled_field='is_delete_locked',
                           hard_delete_description='Its items, packing slip, and delivery history will also be removed.')
        return context


class ChalanCreatePageView(ChalanPageView):
    template_name = 'sale/chalan_form.html'
    permission_required = CHALAN_CREATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_mode='add', page_label='Create Chalan', save_label='Create Chalan',
                       write_api_url=reverse('sale:chalan-list'), options_api_url=reverse('sale:chalan-options'))
        return context


class ChalanEditPageView(ChalanCreatePageView):
    permission_required = CHALAN_EDIT

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.get_record().pk
        context.update(page_mode='edit', page_label='Edit Chalan', save_label='Save changes',
                       write_api_url=reverse('sale:chalan-update', args=[pk]),
                       detail_api_url=reverse('sale:chalan-detail', args=[pk]),
                       options_api_url=reverse('sale:chalan-edit-options'))
        return context


class ChalanDetailPageView(ChalanPageView):
    template_name = 'sale/chalan_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        chalan = self.get_record()
        pk = chalan.pk
        context.update(page_mode='detail', page_label='Chalan details',
                       detail_api_url=reverse('sale:chalan-detail', args=[pk]),
                       is_delivered=chalan.is_delivered,
                       can_view_items=has_role_permissions(self.request.user, CHALAN_ITEM_VIEW))
        if not chalan.is_delivered and has_role_permissions(self.request.user, CHALAN_EDIT):
            context['edit_url'] = reverse('sale_pages:chalan-edit', args=[pk])
        if has_role_permissions(self.request.user, PACKING_SLIP_VIEW):
            slip = PackingSlip.objects.filter(chalan_id=pk).only('slip_number').first()
            if slip:
                context['packing_slip_number'] = slip.slip_number
                if has_role_permissions(self.request.user, PACKING_SLIP_PRINT):
                    context['packing_slip_report'] = {'detail_url': reverse('report:packing-slip-pdf', args=[slip.pk])}
            elif has_role_permissions(self.request.user, PACKING_SLIP_GENERATE):
                context['packing_slip_generate_url'] = reverse('sale:packing-slip-generate', args=[pk])
        if has_role_permissions(self.request.user, DELIVERY_VIEW):
            delivery = getattr(chalan, 'delivery', None)
            if delivery:
                context.update(delivery_url=reverse('sale_pages:delivery-detail', args=[delivery.pk]),
                               delivery_status=delivery.get_status_display())
            elif has_role_permissions(self.request.user, DELIVERY_ASSIGN):
                context['assign_courier_url'] = reverse('sale_pages:delivery-assign', args=[pk])
        return context


class PackingSlipListPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    permission_required = PACKING_SLIP_VIEW
    template_name = 'inventory/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            page_section='sale', sale_area='packing-slips', section_label='Sales', page_mode='list',
            page_label='Packing slip list', list_heading='Packing slip list',
            resource={'singular': 'packing slip', 'plural': 'packing slips'}, hide_list_hint=True,
            list_description='Search by slip number, Chalan number, or customer.',
            list_api_url=reverse('sale:packing-slip-list'),
            view_url_template=reverse('sale_pages:chalan-detail', args=[0]),
            columns=[
                {'key': 'slip_number', 'label': 'Packing slip number', 'type': 'text'},
                {'key': 'chalan_number', 'label': 'Chalan number', 'type': 'text'},
                {'key': 'customer_name', 'label': 'Customer', 'type': 'text'},
                {'key': 'warehouse_name', 'label': 'Warehouse', 'type': 'text'},
                {'key': 'created_at', 'label': 'Generated at', 'type': 'datetime'},
                {'key': 'actions', 'label': 'Actions', 'view_label': 'View Chalan', 'view_id_field': 'chalan'},
            ],
        )
        return context
