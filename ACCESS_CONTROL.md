# Site access control

All management APIs require an active authenticated session plus the permissions
below. HTML management pages enforce the same rules on the server. Anonymous
page visitors go to login; authenticated visitors without permission receive 403.
API denials return 403. Session writes also require valid CSRF tokens.

| Action | Required permissions |
| --- | --- |
| User list / read a user's groups | `auth.view_user` |
| Add user | `auth.add_user` |
| Role list / role detail | `auth.view_group` |
| Add role | `auth.add_group` |
| Rename role | `auth.change_group` |
| Delete role | `auth.delete_group`, `account.manage_access` |
| List available permissions | `auth.view_permission` |
| Assign role (both assignment APIs and page) | `auth.view_user`, `auth.change_user`, `auth.view_group`, `account.manage_access` |
| Edit role permissions (API and page) | `auth.view_group`, `auth.change_group`, `auth.view_permission`, `account.manage_access` |

`ACCESS_RULES` in `account/permissions.py` is the shared policy for APIs, page
views and template visibility. `HasRequiredPermissions` supports a permission
tuple/string or an HTTP-method map. Undeclared supported methods deny access;
HEAD and OPTIONS use the GET rule for method-mapped views.

## Setup

Apply the metadata migration to register the custom permission:

```powershell
.\venv\Scripts\python.exe manage.py migrate
```

Use an existing active superuser to bootstrap access. If none exists, run
`manage.py createsuperuser`. Superusers have all permissions; `is_staff` alone
does not grant application access.

In Roles > Manage permissions, choose permissions for each role. CRUD permissions
appear under their model (for example auth / user / Read). The custom
**Can manage role assignments and role permissions** permission appears under
account / account alongside custom permissions. Grant it only to trusted access
administrators, along with the corresponding model permissions in the table.
An access administrator can grant powerful permissions to other users.

Assign each user one role through Assign role. Assignment replaces the previous
group. Django direct user permissions also remain effective. No permissions are
automatically granted to existing users or groups by the migration. Changes take
effect on subsequent requests without requiring a new login.

## Protected operations

Non-superusers cannot change their own group assignment, a superuser's assignment,
or permissions of a group they belong to. The checks run on both assignment APIs
and applicable HTML pages. Superusers can perform recovery operations.

User/group writes in Django admin are restricted to active superusers to prevent
the built-in forms bypassing these checks. Non-superuser staff can view those
models with their normal view permissions. Account user/email identity fields
are read-only for non-superusers; only superusers can create Account profiles
through Django admin. Application Add user remains controlled by `auth.add_user`.

Dashboard, own profile and logout require login, with no administrative permission.
Login, signup, OTP verification and OTP resend remain public as required for
onboarding. Django admin additionally requires its usual staff admission rules.

## Interface behavior

Navigation and action links are hidden without their required permissions. The
API still checks every request, including requests made directly with Postman.
Permission denials display errors instead of sending an authenticated user into
a login redirect. Create-only users return to the dashboard after saving if they
cannot open the corresponding list. Assignment remains a single-role dropdown.

The database still uses Django's group many-to-many relation. Assignment APIs
enforce one group per user; trusted superusers can edit memberships directly in
Django admin. Existing stored memberships are not silently rewritten.
