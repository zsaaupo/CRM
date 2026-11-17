from dataclasses import dataclass

from .models import Department, Designation, Employee, EmployeeStatus, EmploymentType


@dataclass(frozen=True)
class HRMResource:
    key: str
    path: str
    singular: str
    plural: str
    model: type
    columns: tuple

    def permission(self, action):
        opts = self.model._meta
        return f'{opts.app_label}.{action}_{opts.model_name}'


RESOURCES = (
    HRMResource('employee', 'employees', 'employee', 'employees', Employee,
                (('full_name', 'Employee', 'identity'), ('employee_code', 'Code', 'text'),
                 ('department_name', 'Department', 'text'), ('designation_name', 'Designation', 'text'),
                 ('status_name', 'Status', 'text'), ('warehouse_name', 'Warehouse', 'text'))),
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
RESOURCE_MAP = {resource.key: resource for resource in RESOURCES}
