from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound

from common.serializers import AUDIT_FIELDS
from common.view import RecordInUse
from .leave_balances import approve_leave_days, lock_employees
from .models import Employee, EmployeeCompensation, LeaveRequest, SalaryStructureLine


def lock_salary_record(model, pk):
    """Call in a transaction. The update also acquires a write lock on SQLite."""
    records = model.objects.filter(pk=pk)
    if not records.update(updated_at=F('updated_at')):
        raise NotFound('Salary configuration record not found.')
    return records.get()


def check_basic_salary_line_removal(line):
    if (line.calculation_method == SalaryStructureLine.CalculationMethod.BASIC_SALARY
            and EmployeeCompensation.objects.filter(salary_structure_id=line.salary_structure_id).exists()):
        raise RecordInUse('An assigned salary structure must keep its basic salary line.')


class LeaveAlreadyReviewed(Exception):
    pass


@transaction.atomic
def review_leave_request(request_id, actor, status):
    requests = LeaveRequest.objects.filter(pk=request_id)
    if not lock_employees(requests.values('employee_id')):
        raise LeaveRequest.DoesNotExist
    leave = requests.select_for_update().select_related('employee', 'leave_type').get()
    if leave.employee.user_id == actor.pk:
        raise PermissionDenied('You cannot review your own leave request.')
    if leave.status != LeaveRequest.Status.PENDING:
        if leave.status == status == LeaveRequest.Status.APPROVED and leave.approved_days.exists():
            return leave
        raise LeaveAlreadyReviewed
    if status == LeaveRequest.Status.APPROVED:
        leave._validate_daily_leave = approve_leave_days(leave, actor.get_username())
    leave.status = status
    leave.reviewed_by = actor
    leave.reviewed_at = timezone.now()
    leave.modified_by = actor.get_username()
    leave.full_clean(exclude=AUDIT_FIELDS[1:])
    leave.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'modified_by', 'updated_at'])
    return leave
