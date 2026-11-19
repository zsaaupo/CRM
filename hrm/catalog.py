from dataclasses import dataclass

from .models import (
    Attendance, Department, Designation, Employee, EmployeeAttendanceStatus, EmployeeCompensation, EmployeeLoan,
    EmployeeScheduleAssignment, EmployeeStatus, EmploymentType, Holiday, LeaveRequest, LeaveType, LoanInstallment,
    PayrollAdjustment, PayrollDeductionRule, PayrollRun,
    SalaryComponent, SalaryStructure, SalaryStructureLine, WorkSchedule,
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
SALARY_RESOURCES = (
    HRMResource('salary-component', 'salary-components', 'salary component', 'salary components', SalaryComponent,
                (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('type_name', 'Type', 'text'),
                 ('category', 'Category', 'text'), ('is_active', 'Status', 'status')),
                group='salary', fields='salary_component', has_options=True),
    HRMResource('salary-structure', 'salary-structures', 'salary structure', 'salary structures', SalaryStructure,
                (('name', 'Name', 'text'), ('code', 'Code', 'text'), ('currency', 'Currency', 'text'),
                 ('is_active', 'Status', 'status')),
                group='salary', fields='salary_structure', has_options=True),
    HRMResource('salary-structure-line', 'salary-structure-lines', 'salary structure line', 'salary structure lines',
                SalaryStructureLine,
                (('salary_structure_name', 'Salary structure', 'text'), ('component_name', 'Component', 'text'),
                 ('calculation_method_name', 'Calculation method', 'text'), ('fixed_amount', 'Fixed amount', 'text'),
                 ('percentage', 'Percentage', 'text')),
                group='salary', fields='salary_structure_line', has_options=True),
    HRMResource('employee-compensation', 'employee-compensations', 'employee compensation',
                'employee compensation records', EmployeeCompensation,
                (('employee_name', 'Employee', 'text'), ('salary_structure_name', 'Salary structure', 'text'),
                 ('basic_salary', 'Basic salary', 'price'), ('effective_from', 'Effective from', 'text'),
                 ('effective_to', 'Effective to', 'text')),
                group='salary', fields='employee_compensation', has_options=True),
)
PAYROLL_INPUT_RESOURCES = (
    HRMResource('payroll-adjustment', 'payroll-adjustments', 'payroll adjustment', 'payroll adjustments', PayrollAdjustment,
        (('employee_name', 'Employee', 'text'), ('source_name', 'Source', 'text'), ('component_name', 'Component', 'text'),
         ('effective_on', 'Effective date', 'text'), ('amount', 'Amount', 'price'), ('status_name', 'Status', 'text')),
        group='salary', fields='payroll_adjustment', has_options=True),
    HRMResource('payroll-deduction-rule', 'payroll-deduction-rules', 'payroll deduction rule', 'payroll deduction rules',
        PayrollDeductionRule, (('salary_structure_name', 'Structure', 'text'), ('component_name', 'Component', 'text'),
         ('method_name', 'Calculation', 'text'), ('value', 'Rate or amount', 'text'), ('effective_from', 'Starts', 'text'),
         ('effective_to', 'Ends', 'text'), ('reference', 'Policy reference', 'text')),
        group='salary', fields='payroll_deduction_rule', has_options=True),
    HRMResource('employee-loan', 'employee-loans', 'employee loan', 'employee loans', EmployeeLoan,
        (('employee_name', 'Employee', 'text'), ('reference', 'Reference', 'text'), ('principal', 'Principal', 'price'),
         ('balance', 'Outstanding balance', 'price'), ('disbursed_on', 'Disbursed', 'text')),
        group='salary', fields='employee_loan', has_options=True),
    HRMResource('loan-installment', 'loan-installments', 'loan installment', 'loan installments', LoanInstallment,
        (('loan_reference', 'Loan', 'text'), ('month', 'Due month', 'text'), ('amount', 'Amount', 'price'),
         ('recovered', 'Recovered', 'boolean')),
        group='salary', fields='loan_installment', has_options=True),
)
RESOURCES = (*EMPLOYEE_RESOURCES, *ATTENDANCE_RESOURCES, LEAVE_TYPE_RESOURCE, *SALARY_RESOURCES, *PAYROLL_INPUT_RESOURCES)
PAYROLL_RUN_RESOURCE = HRMResource(
    'payroll-run', 'payroll-runs', 'payroll run', 'payroll runs', PayrollRun,
    (('month', 'Month', 'text'), ('currency', 'Currency', 'text'), ('status_name', 'Status', 'text'),
     ('calculated_at', 'Calculated at', 'datetime'), ('calculated_by', 'Calculated by', 'text')),
    group='salary',
)
RESOURCE_MAP = {resource.key: resource for resource in (*RESOURCES, LEAVE_REQUEST_RESOURCE, PAYROLL_RUN_RESOURCE)}
