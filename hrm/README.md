# HRM models

`Department`, `Designation`, `EmploymentType` and `EmployeeStatus` are configurable
lookup tables with a code, name, description, active flag and audit fields.
The initial migration seeds common employment types and employee statuses;
departments and designations are company-specific and start empty.

`EmployeeStatus` inherits the abstract `common.models.Status`, just like
`ProductStatus` and `TradeStatus`, while storing its records in a separate table.
Its code is a unique slug of up to 32 characters and its name allows 100
characters. It uses the shared status validation; the code trimming and
lowercasing in `HRMLookup` apply to the other three HRM lookup models.

`Employee` stores employment identity, optional photo and work contacts,
department, designation, employment type, status, joining/leaving dates,
reporting manager and workplace. An optional warehouse is a worksite reference.
An optional one-to-one user link connects a login identity. Employees do not
require a login or a commercial `people.Party` record.

Employee worksite, employment status and archival never change the linked
user's login, profile, role or warehouse permissions. User linking and login
offboarding must be explicitly authorized by future HRM APIs.

Call `full_clean()` before saving employees manually. It normalizes employee codes, rejects invalid
dates and reporting cycles, and reserves employee codes across archived records.
Database constraints additionally protect code uniqueness, date ordering and
direct self-reporting. Multi-employee reporting cycles are checked in model
validation, not by a database constraint.

Employee `delete(actor='username')` archives the record; queryset deletion also
archives. `Employee.objects` returns current records and `Employee.all_objects`
includes archived records. Referenced employment lookups, workplaces and linked
users cannot be deleted while retained employee records reference them.

## CRUD APIs

The APIs use the shared `common.view` base classes, session authentication,
CSRF protection and the existing action permissions: `hrm.view_<model>`,
`hrm.add_<model>`, `hrm.change_<model>` and `hrm.delete_<model>`.
Existing Django group grants apply through the project's permission class.

| Resource | Base URL | Model permission suffix |
| --- | --- | --- |
| Employees | `/hrm/api/employees/` | `employee` |
| Departments | `/hrm/api/departments/` | `department` |
| Designations | `/hrm/api/designations/` | `designation` |
| Employment types | `/hrm/api/employment-types/` | `employmenttype` |
| Employee statuses | `/hrm/api/employee-statuses/` | `employeestatus` |

Each base URL supports `GET` (paginated list) and `POST` (create).
Append `list/` for a read-only list, `<id>/` for `GET`, `PUT`, `PATCH` and
`DELETE`, or `<id>/update/` for `PUT` and `PATCH` only. Creation responses wrap
the record in `employee`, `department`, `designation`, `employment_type` or
`employee_status`, alongside a success message. Lists use the standard
`count`, `next`, `previous` and `results` pagination keys.

Lists accept `q` for search and `page` for pagination. Employee APIs also accept
`department`, `designation` and `warehouse` IDs and `employment_type` and
`status` codes as filters. All employee API querysets exclude archives;
there is no API archive list or restore option, including for superusers.

An employee create request can be as small as:

```json
{
  "employee_code": "EMP-001",
  "full_name": "Example Employee"
}
```

Employment type and status default to `full-time` and `active`. To override
them, send their codes. Other relationship fields accept record IDs or null
where optional. Use multipart form data to upload `image`.

Lookup codes are immutable after creation. Deleting a referenced lookup returns
HTTP 409, including when the reference belongs to an archived employee.
Unreferenced lookup deletion removes the record normally. Employee deletion
returns HTTP 204 and records `is_archived`, `archived_at`, `archived_by` and the
modifying actor. The employee then returns HTTP 404 from all record endpoints.
Audit and archive fields cannot be set through create/update requests.

Changing an employee's optional `user` link additionally requires the existing
`auth.change_user` permission. Non-superusers cannot link or unlink a superuser
account. This operation never changes the linked user's credentials or grants.

## Django admin

All five models are registered. Authorized staff can view archives at
`/admin/hrm/employee/` and filter by **Is archived**. Archived employees are
read-only. Admin single-record and bulk employee deletion both archive records
and preserve reporting history. HRM admin forms also enforce the login-link
permission, and admin saves/deletions record the acting username.

## Frontend

The HRM navigation groups employee-related links under **HRM > Employee**:
employees first, followed by departments, designations, employment types and
employee statuses. Only employees have an **Add employee**
menu shortcut. Each list has its own add button and permission-controlled edit
and delete actions, using the project's existing list and form templates.

Pages live under `/hrm/<resource>/`, with `add/` and `<id>/edit/` pages for every
resource. Page access requires view permission plus add/change permission for
the corresponding form. Forms submit to the CSRF-protected CRUD APIs and return
to their list after saving. Employee forms support an optional picture upload.

`GET /hrm/api/employees/options/` provides minimal form choices with add-employee
permission. `GET /hrm/api/employees/<id>/options/` requires view/change permissions
and includes existing inactive references for editing. Lookup choices do not
require lookup management permissions. User choices are available only with
`auth.change_user`, exclude logins linked to other employees (including archives),
and exclude superusers for non-superusers. A historical archived reporting manager
is represented only by an anonymous existing-choice placeholder; other archived
employees cannot be selected. Archived employee edit pages return HTTP 404.

Employee lists also include a view icon for everyone with view permission.
`/hrm/employees/<id>/` loads employee details from the existing detail API,
including the picture, contact and employment information, related labels and
record history. Its edit link requires change permission. Archived or missing
employees return HTTP 404; archived managers' names are not exposed.
