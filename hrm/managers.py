from django.db import models
from django.utils import timezone


class EmployeeQuerySet(models.QuerySet):
    def delete(self, *, actor=None):
        """Archive records without removing employment history or changing login access."""
        now = timezone.now()
        changes = {'is_archived': True, 'archived_at': now, 'archived_by': actor, 'updated_at': now}
        if actor is not None:
            changes['modified_by'] = actor
        count = self.filter(is_archived=False).update(**changes)
        return count, {self.model._meta.label: count}


class EmployeeManager(models.Manager.from_queryset(EmployeeQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_archived=False)
