from django.urls import reverse

from account.page_views import ProtectedAccountPageView

from .service import AUDIT_LOG_VIEW, visible_record_types


class AuditLogPageView(ProtectedAccountPageView):
    template_name = 'audit_log/list.html'
    permission_required = AUDIT_LOG_VIEW

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'page_section': 'audit-log', 'page_label': 'Audit log', 'section_label': 'Audit log',
            'resource': {'singular': 'audit log entry', 'plural': 'audit log entries'}, 'list_heading': 'Audit log',
            'list_description': 'Changes and sign-ins you have permission to view.', 'hide_list_hint': True,
            'list_api_url': reverse('audit_log:api-list'),
            'record_types': visible_record_types(self.request.user),
            'columns': [{'key': key, 'label': label, 'type': kind} for key, label, kind in [
                ('occurred_at', 'Date and time', 'datetime'), ('actor', 'User', 'text'),
                ('action_label', 'Activity', 'text'), ('resource_label', 'Record type', 'text'),
                ('reference', 'Reference', 'text'), ('warehouse_label', 'Warehouse', 'text'),
                ('domain_label', 'Domain', 'text'),
            ]],
        })
        return context
