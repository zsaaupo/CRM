from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from accounting.models import base_currency
from accounting.services import available_payment_accounts, sale_total_in_base_currency
from warehouse.scope import WarehouseScopePageMixin, scoped_queryset, warehouse_scope

from .delivery import REASSIGNABLE_STATUSES, STATUS_TRANSITIONS
from .models import Chalan, Courier, Delivery, DeliveryStatus
from .permissions import (COURIER_CREATE, COURIER_EDIT, COURIER_VIEW, DELIVERY_ASSIGN, DELIVERY_CHANGE,
                          DELIVERY_VIEW, PACKING_SLIP_PRINT, PACKING_SLIP_VIEW)


class CourierPageView(ProtectedAccountPageView):
    permission_required = COURIER_VIEW

    def has_permission(self):
        return has_role_permissions(self.request.user, self.get_permission_required())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_section='sale', sale_area='couriers', section_label='Sales',
                       resource={'singular': 'courier', 'plural': 'couriers'},
                       list_url=reverse('sale_pages:courier-list'), hide_catalog_hint=True, hide_list_hint=True)
        return context


class CourierListPageView(CourierPageView):
    template_name = 'inventory/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context.update(page_mode='list', page_label='Couriers', list_api_url=reverse('sale:courier-list'),
                       add_url=reverse('sale_pages:courier-create'), add_label='Create courier',
                       can_add=has_role_permissions(user, COURIER_CREATE),
                       list_description='Search by courier name, contact, phone, or email.',
                       columns=[{'key': key, 'label': label, 'type': kind} for key, label, kind in (
                           ('name', 'Courier', 'text'), ('contact_person', 'Contact person', 'text'),
                           ('phone', 'Phone', 'text'), ('email', 'Email', 'text'), ('is_active', 'Status', 'status'),
                       )])
        if has_role_permissions(user, COURIER_EDIT):
            context['edit_url_template'] = reverse('sale_pages:courier-edit', args=[0])
            context['columns'].append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
        return context


class CourierCreatePageView(CourierPageView):
    permission_required = COURIER_CREATE
    template_name = 'inventory/add.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_mode='add', page_label='Create courier', form_heading='Create courier',
                       save_label='Create courier', fields_template='sale/includes/courier_fields.html',
                       create_api_url=reverse('sale:courier-list'))
        return context


class CourierEditPageView(CourierCreatePageView):
    permission_required = COURIER_EDIT

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        courier = get_object_or_404(Courier.objects.only('pk'), pk=self.kwargs['pk'])
        api_url = reverse('sale:courier-detail', args=[courier.pk])
        context.update(page_mode='edit', page_label='Edit courier', form_heading='Edit courier',
                       save_label='Save changes', form_method='PATCH', create_api_url=api_url, detail_api_url=api_url)
        return context


class DeliveryPageView(WarehouseScopePageMixin, ProtectedAccountPageView):
    permission_required = DELIVERY_VIEW

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_section='sale', sale_area='deliveries', section_label='Sales',
                       list_url=reverse('sale_pages:delivery-list'))
        return context


class DeliveryListPageView(DeliveryPageView):
    template_name = 'sale/delivery_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            page_mode='list', page_label='Deliveries', resource={'singular': 'delivery', 'plural': 'deliveries'},
            hide_list_hint=True, list_description='Search by Chalan, customer, courier, or tracking number.',
            list_api_url=reverse('sale:delivery-list'),
            view_url_template=reverse('sale_pages:delivery-detail', args=[0]), delivery_statuses=DeliveryStatus.choices,
            columns=[{'key': key, 'label': label, 'type': kind} for key, label, kind in (
                ('chalan_number', 'Chalan number', 'text'), ('customer_name', 'Customer', 'text'),
                ('warehouse_name', 'Warehouse', 'text'), ('courier_name', 'Courier', 'text'),
                ('tracking_number', 'Tracking number', 'text'), ('status_label', 'Status', 'text'),
                ('updated_at', 'Updated at', 'datetime'), ('actions', 'Actions', 'edit'),
            )],
        )
        if has_role_permissions(self.request.user, COURIER_VIEW):
            context.update(secondary_url=reverse('sale_pages:courier-list'), secondary_label='Couriers')
        return context


class DeliveryAssignmentPageView(DeliveryPageView):
    permission_required = DELIVERY_ASSIGN
    template_name = 'sale/delivery_assign.html'

    def get(self, request, *args, **kwargs):
        chalans = Chalan.objects.select_related('customer', 'warehouse', 'delivery')
        self.chalan = get_object_or_404(scoped_queryset(chalans, warehouse_scope(request.user)), pk=kwargs['pk'])
        delivery = getattr(self.chalan, 'delivery', None)
        if delivery:
            return redirect('sale_pages:delivery-detail', pk=delivery.pk)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(page_mode='add', page_label='Assign courier', chalan=self.chalan,
                       assignment_url=reverse('sale:delivery-assign', args=[self.chalan.pk]),
                       delivery_url_template=reverse('sale_pages:delivery-detail', args=[0]),
                       couriers=Courier.objects.filter(is_active=True))
        if has_role_permissions(self.request.user, COURIER_CREATE):
            context['create_courier_url'] = reverse('sale_pages:courier-create')
        return context


class DeliveryDetailPageView(DeliveryPageView):
    template_name = 'sale/delivery_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        scope = warehouse_scope(user)
        records = Delivery.objects.select_related('chalan__customer', 'chalan__warehouse', 'chalan__packing_slip',
                                                  'chalan__delivery_payment__account', 'courier')
        delivery = get_object_or_404(scoped_queryset(records, scope, 'chalan__warehouse_id'), pk=self.kwargs['pk'])
        slip = getattr(delivery.chalan, 'packing_slip', None)
        context.update(page_mode='detail', page_label='Delivery details', delivery=delivery,
                       status_updates=delivery.status_updates.all(),
                       payment=getattr(delivery.chalan, 'delivery_payment', None))
        if slip and has_role_permissions(user, PACKING_SLIP_VIEW):
            context['packing_slip_number'] = slip.slip_number
            if has_role_permissions(user, PACKING_SLIP_PRINT):
                context['packing_slip_report'] = {'detail_url': reverse('report:packing-slip-pdf', args=[slip.pk])}
        if has_role_permissions(user, DELIVERY_CHANGE):
            context['update_url'] = reverse('sale:delivery-detail', args=[delivery.pk])
            if delivery.status in REASSIGNABLE_STATUSES:
                context['couriers'] = Courier.objects.filter(Q(is_active=True) | Q(pk=delivery.courier_id))
            options = STATUS_TRANSITIONS.get(delivery.status, ())
            if DeliveryStatus.HANDED_OVER in options and (slip is None or not delivery.courier.is_active):
                context['needs_packing_slip'] = slip is None
                context['needs_active_courier'] = not delivery.courier.is_active
                options = tuple(option for option in options if option != DeliveryStatus.HANDED_OVER)
            if options:
                context.update(status_url=reverse('sale:delivery-status', args=[delivery.pk]), status_options=options)
            if DeliveryStatus.DELIVERED in options:
                amount = sale_total_in_base_currency(delivery.chalan)
                if amount > 0:
                    accounts = available_payment_accounts(scope).filter(
                        Q(warehouse_id=delivery.chalan.warehouse_id) | Q(warehouse__isnull=True),
                    ).order_by('name', 'pk')
                    context.update(delivery_payment_due=amount, payment_currency=base_currency(), receiving_accounts=accounts)
        return context
