from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    actor = models.CharField(max_length=255, blank=True)
    resource = models.CharField(max_length=80)
    resource_label = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    reference = models.CharField(max_length=120)
    action = models.CharField(max_length=40)
    # Snapshot IDs preserve branch scope after a record or a warehouse is deleted.
    warehouse_id = models.PositiveBigIntegerField(null=True)
    related_warehouse_id = models.PositiveBigIntegerField(null=True)
    subject_id = models.PositiveBigIntegerField(null=True)
    all_warehouses = models.BooleanField(default=False)
    protected_role = models.BooleanField(default=False)

    class Meta:
        default_permissions = ('view',)
        ordering = ['-occurred_at', '-pk']
        indexes = [
            models.Index(fields=['-occurred_at', '-id'], name='audit_log_date_idx'),
            models.Index(fields=['warehouse_id', '-occurred_at'], name='audit_log_branch_date_idx'),
            models.Index(fields=['related_warehouse_id', '-occurred_at'], name='audit_log_related_date_idx'),
        ]
