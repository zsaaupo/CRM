from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from account.page_views import ProtectedAccountPageView
from .catalog import RESOURCE_MAP
from .models import Employee


class HRMPageView(ProtectedAccountPageView):
    resource_key = None
    mode = 'list'

    @property
    def resource(self):
        if self.resource_key not in RESOURCE_MAP:
            raise ImproperlyConfigured('Declare a registered HRM resource.')
        return RESOURCE_MAP[self.resource_key]

    def get_permission_required(self):
        permissions = [self.resource.permission('view')]
        if self.mode == 'add':
            permissions.append(self.resource.permission('add'))
        elif self.mode == 'edit':
            permissions.append(self.resource.permission('change'))
        return permissions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        resource, user = self.resource, self.request.user
        context.update({
            'resource': resource, 'page_section': 'hrm', 'section_label': 'HRM',
            'hrm_key': resource.key, 'page_mode': self.mode,
            'page_label': f'{resource.singular.capitalize()} list',
            'list_url': reverse(f'hrm_pages:{resource.key}-list'),
            'add_url': reverse(f'hrm_pages:add-{resource.key}'),
            'list_api_url': reverse(f'hrm:{resource.key}-list-view'),
            'can_add': user.has_perm(resource.permission('add')),
            'can_delete': user.has_perm(resource.permission('delete')),
            'list_description': f'Your {resource.plural}, in one place.',
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in resource.columns],
        })
        if resource.key == 'employee':
            context.update({'soft_delete': True,
                            'soft_delete_description': 'The employee record will be retained.'})
        can_change = user.has_perm(resource.permission('change'))
        if resource.key == 'employee' or can_change or context['can_delete']:
            context['columns'].append({'key': 'actions', 'label': 'Actions', 'type': 'edit'})
        if resource.key == 'employee':
            context['view_url_template'] = reverse('hrm_pages:employee-detail', args=[0])
        if can_change:
            context['edit_url_template'] = reverse(f'hrm_pages:edit-{resource.key}', args=[0])
        if context['can_delete']:
            context['delete_url_template'] = reverse(f'hrm:{resource.key}-detail', args=[0])
        return context


class HRMListPageView(HRMPageView):
    template_name = 'inventory/list.html'


class EmployeeDetailPageView(HRMPageView):
    template_name = 'hrm/employee_detail.html'
    resource_key = 'employee'
    mode = 'detail'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = get_object_or_404(Employee, pk=self.kwargs['pk'])
        context.update({
            'page_label': 'Employee details',
            'detail_api_url': reverse('hrm:employee-detail', args=[employee.pk]),
        })
        if self.request.user.has_perm('hrm.change_employee'):
            context['edit_url'] = reverse('hrm_pages:edit-employee', args=[employee.pk])
        return context


class HRMAddPageView(HRMPageView):
    template_name = 'inventory/add.html'
    mode = 'add'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.resource_key == 'employee'
        heading = f'Add {self.resource.singular}'
        context.update({
            'page_label': heading, 'form_heading': heading, 'save_label': heading,
            'create_api_url': reverse(f'hrm:{self.resource.key}-list'),
            'fields_template': 'hrm/fields/employee.html' if employee else 'hrm/fields/lookup.html',
            'form_description': 'Enter employment details and a work contact.' if employee else
                                f'Create a {self.resource.singular} for your team.',
            'options_api_url': reverse('hrm:employee-options') if employee else '',
            'can_link_user': self.request.user.has_perm('auth.change_user'),
            'joining_date_default': timezone.localdate().isoformat(),
            'code_maxlength': 32 if self.resource_key == 'employee-status' else 40,
            'name_maxlength': 100 if self.resource_key == 'employee-status' else 150,
        })
        return context


class HRMEditPageView(HRMAddPageView):
    mode = 'edit'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = get_object_or_404(self.resource.model, pk=self.kwargs['pk'])
        heading = f'Edit {self.resource.singular}'
        context.update({
            'page_label': heading, 'form_heading': heading, 'save_label': 'Save changes',
            'form_method': 'PATCH', 'lock_code': self.resource_key != 'employee',
            'create_api_url': reverse(f'hrm:{self.resource.key}-update', args=[target.pk]),
            'detail_api_url': reverse(f'hrm:{self.resource.key}-detail', args=[target.pk]),
            'form_description': f'Update your {self.resource.singular} details.',
        })
        if isinstance(target, Employee):
            context['options_api_url'] = reverse('hrm:employee-edit-options', args=[target.pk])
            if target.user_id and target.user.is_superuser and not self.request.user.is_superuser:
                context['can_link_user'] = False
        return context
