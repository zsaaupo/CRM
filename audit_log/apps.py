from django.apps import AppConfig


class AuditLogConfig(AppConfig):
    name = 'audit_log'
    verbose_name = 'Audit log'

    def ready(self):
        from .service import connect_signals

        connect_signals()
