from django.conf import settings
from django.shortcuts import get_object_or_404
from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from account.permissions import has_role_permissions
from inventory.catalog import CatalogResource
from warehouse.models import WareHouse
from warehouse.scope import all_warehouse_access, has_warehouse_scope, scoped_queryset, warehouse_scope

from .models import Expense, ExpenseCategory


CATEGORIES = CatalogResource(
    'expense-category', 'expense-categories', 'expense category', 'expense categories', ExpenseCategory,
    (('name', 'Name', 'text'), ('is_active', 'Status', 'status')),
)
EXPENSES = CatalogResource(
    'expense', 'expenses', 'expense', 'expenses', Expense,
    (('date', 'Date', 'text'), ('category_name', 'Category', 'text'), ('warehouse_name', 'Warehouse', 'text'),
     ('amount', 'Amount', 'price')),
)


class AccountingPageView(ProtectedAccountPageView):
    resource = None
    mode = 'list'

    def can(self, action):
        user = self.request.user
        permission = self.resource.permission(action)
        if self.resource is EXPENSES:
            return has_role_permissions(user, (permission,))
        return user.has_perm(permission)

    def get_permission_required(self):
        action = {'add': 'add', 'edit': 'change'}.get(self.mode)
        permissions = [self.resource.permission('view')]
        if action:
            permissions.append(self.resource.permission(action))
        return permissions

    def has_permission(self):
        if self.resource is EXPENSES:
            return has_role_permissions(self.request.user, self.get_permission_required()) and has_warehouse_scope(self.request.user)
        return super().has_permission()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        key = self.resource.key
        context.update(
            resource=self.resource, page_section='expense', expense_key=key, section_label='Expenses',
            page_mode=self.mode, list_url=reverse(f'accounting_pages:{key}-list'),
        )
        return context


class AccountingListPageView(AccountingPageView):
    template_name = 'inventory/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        resource = self.resource
        can_change, can_delete = self.can('change'), self.can('delete')
        context.update(
            page_label=resource.plural.capitalize(), list_api_url=reverse(f'accounting:{resource.key}-list'),
            add_url=reverse(f'accounting_pages:add-{resource.key}'), can_add=self.can('add'), can_delete=can_delete,
            list_description=('Expenses by category and warehouse.' if resource is EXPENSES else
                              'Categories used to organize expenses.'),
            columns=[{'key': key, 'label': label, 'type': kind} for key, label, kind in resource.columns],
        )
        if can_change or can_delete:
            context['columns'].append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
        if can_change:
            context['edit_url_template'] = reverse(f'accounting_pages:edit-{resource.key}', args=[0])
        if can_delete:
            context['delete_url_template'] = reverse(f'accounting:{resource.key}-detail', args=[0])
        if resource is EXPENSES:
            scope = warehouse_scope(self.request.user)
            context.update(
                filter_categories=ExpenseCategory.objects.all(),
                filter_warehouses=scoped_queryset(WareHouse.objects.order_by('name', 'pk'), scope, 'pk'),
            )
        return context


class AccountingFormPageView(AccountingPageView):
    template_name = 'inventory/add.html'
    mode = 'add'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        resource = self.resource
        editing = self.mode == 'edit'
        heading = f'Edit {resource.singular}' if editing else f'Add {resource.singular}'
        context.update(
            page_label=heading, form_heading=heading, save_label='Save changes' if editing else heading,
            fields_template=f'accounting/fields/{resource.key}.html', hide_catalog_hint=True,
            create_api_url=reverse(f'accounting:{resource.key}-list'),
        )
        if resource is EXPENSES:
            scope = warehouse_scope(self.request.user)
            restricted = not all_warehouse_access(scope)
            context.update(
                amount_label=f'Amount ({settings.INVENTORY_BASE_CURRENCY})',
                assigned_warehouse=scope.warehouse_id if restricted else '',
                warehouse_locked=restricted, options_api_url=reverse('accounting:expense-options'),
            )
        if editing:
            queryset = resource.model.objects.all()
            if resource is EXPENSES:
                queryset = scoped_queryset(queryset, scope)
            record = get_object_or_404(queryset, pk=self.kwargs['pk'])
            endpoint = reverse(f'accounting:{resource.key}-detail', args=[record.pk])
            context.update(form_method='PATCH', create_api_url=endpoint, detail_api_url=endpoint)
            if resource is EXPENSES:
                context['options_api_url'] = reverse('accounting:expense-edit-options', args=[record.pk])
        return context
