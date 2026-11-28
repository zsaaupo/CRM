from urllib.parse import urlencode

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from account.page_views import ProtectedAccountPageView

from .permissions import CORRECT_PROMOTIONS, RECORD_PROMOTIONS, VIEW_PROMOTIONS
from .promotion_api import DesignationHistorySerializer, history_queryset
from .promotions import HISTORY, business_date


class PromotionPageView(ProtectedAccountPageView):
    permission_required = (VIEW_PROMOTIONS,)

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            page_section='hrm', section_label='HRM', hrm_group='employee', hrm_key='promotions',
            resource={'singular': 'designation change', 'plural': 'designation changes'},
            list_url=reverse('hrm_pages:promotion-list'), **kwargs,
        )


class PromotionListPageView(PromotionPageView):
    template_name = 'inventory/list.html'

    def get_context_data(self, **kwargs):
        api_url = reverse('hrm:promotion-list')
        employee = self.request.GET.get('employee_id')
        if employee:
            api_url += '?' + urlencode({'employee_id': employee})
        return super().get_context_data(
            page_label='Promotions and designation history', list_heading='Promotions and designation history',
            list_api_url=api_url, hide_list_hint=True,
            list_description='Review recorded and scheduled designation changes.',
            can_add=self.request.user.has_perms(RECORD_PROMOTIONS),
            add_url=reverse('hrm_pages:add-promotion'), add_label='Record change',
            view_url_template=reverse('hrm_pages:promotion-detail', args=[0]),
            columns=[{'key': key, 'label': label, 'type': kind} for key, label, kind in (
                ('employee_name', 'Employee', 'text'), ('employee_code', 'Code', 'text'),
                ('previous_designation_name', 'Previous designation', 'text'), ('designation_name', 'Designation', 'text'),
                ('effective_date', 'Effective date', 'text'), ('status', 'Status', 'text'), ('actions', 'Actions', 'edit'),
            )], **kwargs,
        )


class PromotionDetailPageView(PromotionPageView):
    template_name = 'hrm/promotion_detail.html'

    def get_context_data(self, **kwargs):
        entry = get_object_or_404(history_queryset(), pk=self.kwargs['pk'])
        can_correct = (entry.kind != HISTORY.OPENING and not entry.is_superseded
                       and self.request.user.has_perms(CORRECT_PROMOTIONS))
        return super().get_context_data(
            page_label='Designation change', entry=DesignationHistorySerializer(entry).data,
            correction_url=reverse('hrm_pages:correct-promotion', args=[entry.pk]) if can_correct else '', **kwargs,
        )


class PromotionAddPageView(PromotionPageView):
    template_name = 'inventory/add.html'
    permission_required = RECORD_PROMOTIONS

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            page_label='Record designation change', page_mode='add', form_heading='Record designation change',
            form_description='Select the employee, new designation and date it takes effect.',
            save_label='Record change', today=business_date().isoformat(),
            create_api_url=reverse('hrm:promotion-list'), options_api_url=reverse('hrm:promotion-options'),
            fields_template='hrm/fields/promotion.html', hide_catalog_hint=True, **kwargs,
        )


class PromotionCorrectionPageView(PromotionPageView):
    template_name = 'inventory/add.html'
    permission_required = CORRECT_PROMOTIONS

    def get_context_data(self, **kwargs):
        entry = get_object_or_404(history_queryset(), pk=self.kwargs['pk'])
        if entry.kind == HISTORY.OPENING or entry.is_superseded:
            raise Http404('Choose the latest revision of a designation change.')
        return super().get_context_data(
            page_label='Correct designation change', form_heading='Correct designation change',
            form_description=f'{entry.employee_name} ({entry.employee_code}) · {entry.effective_date}',
            save_label='Record correction', correcting=True,
            create_api_url=reverse('hrm:promotion-correction', args=[entry.pk]),
            options_api_url=reverse('hrm:promotion-correction-options'),
            fields_template='hrm/fields/promotion.html', hide_catalog_hint=True, **kwargs,
        )
