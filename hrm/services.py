from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from common.serializers import AUDIT_FIELDS
from .models import Employee, LeaveRequest


class LeaveAlreadyReviewed(Exception):
    pass


@transaction.atomic
def review_leave_request(request_id, actor, status):
    requests = LeaveRequest.objects.filter(pk=request_id)
    # A write also acquires the lock on SQLite, where select_for_update() has no effect.
    if not requests.update(updated_at=F('updated_at')):
        raise LeaveRequest.DoesNotExist
    leave = requests.select_related('employee', 'leave_type').get()
    if leave.employee.user_id == actor.pk:
        raise PermissionDenied('You cannot review your own leave request.')
    if leave.status != LeaveRequest.Status.PENDING:
        raise LeaveAlreadyReviewed
    if status == LeaveRequest.Status.APPROVED:
        # Serialize overlap checks across requests for the same employee.
        Employee.all_objects.filter(pk=leave.employee_id).update(updated_at=F('updated_at'))
    leave.status = status
    leave.reviewed_by = actor
    leave.reviewed_at = timezone.now()
    leave.modified_by = actor.get_username()
    leave.full_clean(exclude=AUDIT_FIELDS[1:])
    leave.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'modified_by', 'updated_at'])
    return leave
