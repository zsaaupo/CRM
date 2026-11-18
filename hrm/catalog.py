from dataclasses import dataclass

from .models import (
    Attendance, Department, Designation, Employee, EmployeeAttendanceStatus, EmployeeScheduleAssignment,
    EmployeeStatus, EmploymentType, Holiday, LeaveRequest, LeaveType, WorkSchedule,
)


@dataclass(frozen=True)
class HRMResource:
    key: str
    path: str
    singular: str
    plural: str
    model: type
    columns: tuple
    group: str = 'employee'
    fields: str = 'lookup'
    has_options: bool = False

    def permission(self, action):
        opts = self.model._meta
        return f'{opts.app_label}.{action}_{opts.model_name}'


EMPLOYEE_RESOURCES = (
    HRMResource('employee', 'employees', 'employee', 'employees', Employee,
                (('full_name', 'Employee', 'identity'), ('employee_code', 'Code', 'text'),
                 ('department_name', 'Department', 'text'), ('designation_name', 'Designation', 'text'),
                 ('status_name', 'Status', 'text'), ('warehouse_name', 'Warehouse', 'text')),
                fields='employee', has_options=True),
    HRMResource('department', 'departments', 'department', 'departments', Department,
                (('name', 'Name', 'text'), ('code', 'Code', 'text'),
                 ('description', 'Description', 'text'), ('is_active', 'Status', 'status'))),
    HRMResource('designation', 'designations', 'designation', 'designations', Designation,
                (('name', 'Name', 'text'), ('code', 'Code', 'text'),
                 ('description', 'Description', 'text'), ('is_active', 'Status', 'status'))),
    HRMResource('employment-type', 'employment-types', 'employment type', 'employment types', EmploymentType,
                (('name', 'Name', 'text'), ('code', 'Code', 'text'),
                 ('description', 'Description', 'text'), ('is_active', 'Status', 'status'))),
    HRMResource('employee-status', 'employee-statuses', 'employee status', 'employee statuses', EmployeeStatus,
                (('name', 'Name', 'text'), ('code', 'Code', 'text'),
                 ('description', 'Description', 'text'), ('is_active', 'Status', 'status'))),
)
ATTENDANCE_RESOURCES = (
    HRMResource('attendance', 'attendance', 'attendance record', 'attendance records', Attendance,
                (('work_date', 'Work date', 'text'), ('employee_name', 'Employee', 'text'),
                 ('status_name', 'Status', 'text'), ('check_in', 'Check-in', 'datetime'),
                 ('check_out', 'Check-out', 'datetime')),
                group='attendance', fields='attendance', has_options=True),
    HRMResource('work-schedule', 'work-schedules', 'work schedule', 'work schedules', WorkSchedule,
                (('name', 'Schedule', 'text'), ('code', 'Code', 'text'), ('working_days', 'Working days', 'weekdays'),
                 ('start_time', 'Start', 'text'), ('end_time', 'End', 'text'), ('is_active', 'Status', 'status')),
                group='attendance', fields='work_schedule'),
    HRMResource('employee-schedule-assignment', 'employee-schedule-assignments', 'schedule assignment',
                'schedule assignments', EmployeeScheduleAssignment,
                (('employee_name', 'Employee', 'text'), ('schedule_name', 'Schedule', 'text'),
                 ('effective_from', 'Effective from', 'text'), ('effective_to', 'Effective to', 'text')),
                group='attendance', fields='schedule_assignment', has_options=True),
    HRMResource('holiday', 'holidays', 'holiday', 'holidays', Holiday,
                (('date', 'Date', 'text'), ('name', 'Holiday', 'text'), ('warehouse_name', 'Warehouse', 'text')),
                group='attendance', fields='holiday', has_options=True),
    HRMResource('employee-attendance-status', 'employee-attendance-statuses', 'attendance status', 'attendance statuses',
                EmployeeAttendanceStatus,
                (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('description', 'Description', 'text'),
                 ('is_active', 'Status', 'status')), group='attendance'),
)
LEAVE_REQUEST_RESOURCE = HRMResource(
    'leave-request', 'leave-requests', 'leave request', 'leave requests', LeaveRequest,
    (('employee_name', 'Employee', 'text'), ('leave_type_name', 'Leave type', 'text'),
     ('start_date', 'Start date', 'text'), ('end_date', 'End date', 'text'), ('status_name', 'Status', 'text')),
    group='leave', fields='leave_request', has_options=True,
)
LEAVE_TYPE_RESOURCE = HRMResource(
    'leave-type', 'leave-types', 'leave type', 'leave types', LeaveType,
    (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('is_paid', 'Paid', 'boolean'), ('is_active', 'Status', 'status')),
    group='leave', fields='leave_type',
)
RESOURCES = (*EMPLOYEE_RESOURCES, *ATTENDANCE_RESOURCES, LEAVE_TYPE_RESOURCE)
RESOURCE_MAP = {resource.key: resource for resource in (*RESOURCES, LEAVE_REQUEST_RESOURCE)}
