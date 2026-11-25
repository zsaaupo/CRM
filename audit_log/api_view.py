from common.view import BaseListAPIView

from .service import AUDIT_LOG_VIEW, AuditLogSerializer, audit_log_queryset, filter_audit_logs


class AuditLogListAPIView(BaseListAPIView):
    queryset = audit_log_queryset()
    serializer_class = AuditLogSerializer
    required_permissions = AUDIT_LOG_VIEW
    ordering = ['-occurred_at', '-pk']
    search_fields = ['actor', 'action', 'reference', 'resource_label', 'warehouse_name', 'related_warehouse_name']

    def get_queryset(self):
        return filter_audit_logs(super().get_queryset(), self.request.user, self.request.query_params)
