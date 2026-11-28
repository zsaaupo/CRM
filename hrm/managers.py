from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class EmployeeQuerySet(models.QuerySet):
    def delete(self, *, actor=None):
        """Archive records without removing employment history or changing login access."""
        from audit_log.service import soft_delete_with_audit

        now = timezone.now()
        changes = {'is_archived': True, 'archived_at': now, 'archived_by': actor, 'updated_at': now}
        if actor is not None:
            changes['modified_by'] = actor
        count = soft_delete_with_audit(self.filter(is_archived=False), 'is_archived', changes, 'archived', actor)
        return count, {self.model._meta.label: count}


class EmployeeManager(models.Manager.from_queryset(EmployeeQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_archived=False)


class EmployeeDesignationChangeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Designation history is append-only.')

    def delete(self):
        raise ValidationError('Designation history is append-only.')
