# Session login API

All management endpoints now enforce permissions. See [ACCESS_CONTROL.md](ACCESS_CONTROL.md)
for the full permission matrix, migration and administrator setup.

## Account profile images

`POST /account/api/add-user/` accepts an optional `image` file.
Use multipart/form-data with the normal add-user
fields as Text and `image` as File. Let the client set the multipart Content-Type
boundary. JSON without an image remains supported; JSON cannot contain a file
upload. Invalid images receive HTTP 400 with an `image` field error.

Signup does not support image uploads. Submitted image fields are ignored;
new signup accounts start without an image.

Signup, add-user and login account responses include `account.image` as a media
URL or null. User-list results include the same value as `image`. Login accepts
credentials only and returns the existing saved image. Verification preserves
the image. OTP, logout and role/group endpoints keep their existing message or
assignment responses, since they do not return account profiles.

## Obtain a CSRF token

Send `GET /account/api/login/`. The response contains `csrfToken` and sets a
`csrftoken` cookie. Keep that cookie in your HTTP client's cookie jar.

## Log in

Send `POST /account/api/login/` with `Content-Type: application/json` and an
`X-CSRFToken` header containing the token from the GET response:

```json
{
  "email": "person@example.com",
  "password": "your-existing-password"
}
```

The email is normalized to lowercase and used as the username, matching signup.
Only active users can log in. Signup users become active after OTP verification.
The serializer validates input; the API view calls Django `authenticate()` and
`login()`. Password-strength validation is not applied again at login.

- HTTP 200: authentication succeeded; the response sets a session cookie and
  contains a message, user ID/email, account data (or `null` for users without a
  profile), and a new `csrfToken`.
- HTTP 400: required fields or input format are invalid.
- HTTP 401: `Invalid email or password.` for wrong credentials or inactive users.
- HTTP 403: CSRF cookie/header is missing, invalid, or fails the origin check.
  Django handles CSRF failures before the API view, so these responses may be HTML.

Django rotates the CSRF secret when login succeeds. Use the returned new token
for subsequent state-changing requests, or obtain a fresh token after navigation.
Send the session cookie on later requests; same-origin browser fetch requests
already do this through the shared helper. Authentication responses are not cached.

In Postman, first GET the endpoint, copy `csrfToken`, and set `X-CSRFToken` on the
POST. Postman should retain both CSRF and session cookies automatically. In Django
templates, `{% csrf_token %}` supplies the initial token instead of the GET step.

The browser login page is `/account/login/`. It submits through fetch and navigates
to the protected `/dashboard/` on success. Already-authenticated users visiting the
login page are redirected to the dashboard. Protected HTML pages use Django's
`LoginRequiredMixin` and are marked non-cacheable.

## Add a user

Send `POST /account/api/add-user/` with the logged-in session cookie and current
`X-CSRFToken` header. Requires authentication and `auth.add_user`.
Staff status alone is insufficient.

```json
{
  "full_name": "New Person",
  "email": "new.person@example.com",
  "password": "R8!xZ2#kT6@pM4$v",
  "password_confirm": "R8!xZ2#kT6@pM4$v",
  "phone_number": "+8801700000000",
  "designation": "Teacher"
}
```

Name, email, password and matching confirmation are required. Phone and profile
fields (`date_of_birth`, `gender`, `designation`, `address`, `city`, `state`,
`postal_code`, `country`) are optional. Gender accepts `M`, `F` or `T`.
An optional `image` file is also accepted. For image uploads, select Postman's
Body > form-data, add the normal fields as Text, and add `image` as File.
Do not manually set Content-Type: let Postman generate the multipart boundary.
Keep the session cookies and `X-CSRFToken` header. JSON requests without an image
remain supported. Invalid images return HTTP 400 with an `image` field error.
Images are stored under `media/profile_pictures/`; the account response includes
the `image` media URL, or null when no image is set.
Email is normalized and checked for duplicates; Django password validation applies.
Only declared fields are accepted for creation, so callers cannot assign staff,
superuser, groups, permissions, user IDs or OTP values.

Success returns HTTP 201 with a message and serialized account. Both database
records are created atomically and the password is hashed. This provisioning
endpoint creates an active user without an OTP or email; the new user can log in
immediately. The creator's session remains unchanged. Invalid input returns 400;
anonymous requests or session requests without valid CSRF return 403.

## List users

Send `GET /account/api/users/` with a logged-in session cookie. The view uses
`IsAuthenticated` and the reusable `HasRequiredPermissions` class, requiring
`auth.view_user` (auth → user → Read in the role permission matrix).
Assign this permission to the user's group to grant access. Django's direct user
permissions and active-superuser override are supported too; staff status alone
does not grant access. Missing permissions return HTTP 403. Group membership and
permission changes take effect on the next request without logging in again.
The directory includes active and inactive users, including users without an
Account profile.

The response contains `count`, `next`, `previous`, and `results`, with 20 users
per page, newest first. Each result exposes only `id`, `full_name`, `email`,
`image`, `phone_number`, `designation`, `is_active`, and `date_joined`.
`image` contains the profile image media URL or null for users without an image
or Account profile. Passwords, OTPs,
birth dates, addresses, and permissions are not included.

Use `?page=2` for later pages or `?q=avery` to search by name, email, phone or
designation. Search is case-insensitive. An empty search result returns HTTP 200
with an empty `results` array; an invalid page returns 404. Anonymous requests
return 403. This GET request does not require a CSRF header.

The protected `/account/add-user/` page calls the add-user API with fetch and
redirects to `/account/users/` on success when the creator can view users, or to
the dashboard otherwise. The list page fetches this endpoint
and supports search, pagination, loading, empty, and retry states. The HTML user
list requires the same `auth.view_user` permission: anonymous visitors go to login,
and authenticated visitors without permission receive 403. The sidebar User list
link is only shown to users with access. If an open list loses permission, its
next API request displays an access-denied message instead of redirecting to login.
All other management APIs and pages now enforce the permissions in
[ACCESS_CONTROL.md](ACCESS_CONTROL.md). Apply migration 0011 for the new
`account.manage_access` permission.

## List permissions

Send `GET /account/api/permissions/` with a logged-in session cookie. The endpoint
uses `SessionAuthentication`, `IsAuthenticated` and `HasRequiredPermissions`.
The caller must have `auth.view_permission`.
GET requests do not need a CSRF header or request body.

The response is a JSON array of all Django permissions, including custom
permissions. Each item contains `id`, `name`, `codename`, `content_type` (the
content-type ID), `app_label`, and `model`. Results are ordered by app, model,
and codename. There is no pagination. This lists available permissions rather
than only permissions assigned to the current user, and does not assign any.
Anonymous requests return 403; POST is not supported.

## List groups

Send `GET /account/api/groups/` with a logged-in session cookie and
`auth.view_group`. Authentication and permission checks are both required.
No request body or CSRF header is needed for this GET request.

The response is a JSON array of all Django groups, ordered by name, without
pagination. Each item contains `id`, `name`, and `permissions` (a list of
permission IDs matching the permission-list endpoint). Groups without permissions
have an empty list; no groups returns `[]`. Group membership is not required to
list a group. GET does not assign memberships or permissions. Anonymous requests
return 403.

## Create a group

Send `POST /account/api/groups/` with a logged-in session cookie and the current
`X-CSRFToken` header:

```json
{
  "name": "Teachers"
}
```

Requires authentication and `auth.add_group`. Names are required,
trimmed, nonblank, unique and at most 150 characters. Invalid or duplicate names
return HTTP 400 with a `name` field error. Anonymous or inactive sessions and
requests without valid CSRF receive HTTP 403.

Success returns HTTP 201 with the new group's `id`, `name`, and `permissions: []`.
Creation does not assign users or permissions; `id` and `permissions` are read-only.
Use the existing group-permissions PUT endpoint to assign permissions separately.
No database migration is required.

## Manage roles

Roles use Django groups. The protected `/account/roles/` page loads the group
list API and displays each role's name with Edit, Manage permissions and Delete
icon actions. The sidebar has an expandable Roles & Permissions section.

`GET /account/api/groups/<id>/` returns one group's ID, name and permission IDs.
`PATCH` on the same URL renames it with `{"name": "Teachers"}`. Names are
required, nonblank, unique and at most 150 characters. Permission IDs are read-only
on this endpoint. `DELETE` removes the group and returns 204 with no body; it
removes group memberships without deleting user accounts or permission records.
GET requires `auth.view_group`; PATCH requires `auth.change_group`; DELETE
requires both `auth.delete_group` and `account.manage_access`. All require
authentication; PATCH and DELETE also require CSRF.
Missing groups return 404. The UI confirms deletion before making the request.

Manage permissions opens `/account/roles/<id>/permissions/`. It fetches the
permission catalog and group detail, then groups permissions by content-type ID
so models with the same name in different apps remain separate. Each model gets
Create (`add`), Read (`view`), Update (`change`) and Delete columns; custom
permissions appear alongside them. Missing CRUD permissions display a dash.
Assigned permissions are checked. Checkbox values are permission IDs; the page
shows a selection count and supports resetting to the loaded assignments.
Save permissions submits all checked permission IDs through fetch to the
group-permissions PUT endpoint with a CSRF token. After HTTP 200, the browser
redirects to the role list. Failures display an error without clearing the
selection. Save, Reset and the checkboxes are disabled during the request to
prevent repeated or conflicting submissions. Unchecking every permission sends
an empty list to clear the role's assignments.

## Replace group permissions

Send `PUT /account/api/groups/<id>/permissions/` with a logged-in session cookie
and the current `X-CSRFToken` header:

```json
{
  "permissions": [1, 2, 5, 8]
}
```

Use real IDs from the permission list API. The `permissions` field is required
and must be a list of existing positive integer permission IDs. Duplicate IDs
are removed. Any invalid ID rejects the entire request with HTTP 400 without
changing existing assignments. An empty list explicitly clears all permissions.

The endpoint replaces the group's entire permission selection, including custom
permissions. Repeating a request has the same result. HTTP 200 returns the group's
`id`, `name` and saved `permissions` IDs. It does not rename groups, change their
memberships or alter direct user permissions. Missing groups return 404.

Authentication is `SessionAuthentication`. The caller needs `auth.view_group`,
`auth.change_group`, `auth.view_permission` and `account.manage_access`.
Non-superusers cannot change permissions of a group they belong to.
Requests without a valid session/CSRF token receive 403. Only PUT and OPTIONS are supported;
read current assignments through the existing group-detail GET endpoint.

Replacement uses `transaction.atomic()` and locks the group with
`select_for_update()` on databases that support row locking. Assignment writers
should use the same locking convention. SQLite does not provide these row locks.
There is no revision check, so a later completed save can replace an earlier
selection. The permission page connects to this endpoint and redirects to
`/account/roles/` after a successful save.

## Assign one group to a user

Send `POST /account/api/assign-group/` with a logged-in session cookie and
the current `X-CSRFToken` header. The request contains one user ID and one group ID:

```json
{"user": 7, "group": 2}
```

Success returns HTTP 200:

```json
{"user": 7, "group": 2}
```

Assignment replaces all previous group memberships with exactly the selected
group. Repeating the request does not add another membership. Both IDs are
required positive integers; lists and null values are rejected. Invalid payloads
or nonexistent groups return 400 without changing assignments; missing users
return 404. Group IDs are available from `GET /account/api/groups/`.

The existing `PUT /account/api/users/<id>/groups/` endpoint also accepts only
`{"group": 2}` and replaces the user's group with that one group. The old
`groups` array payload is no longer accepted, including empty arrays.
`GET` on that endpoint still returns `{"user_id": 7, "groups": [2]}` to inspect
stored memberships. Existing memberships are replaced when the user is assigned
a group; no existing data is modified automatically. This API rule does not change
Django's built-in many-to-many group model. User/group writes in Django admin
are restricted to superusers to prevent bypassing application checks.
Users do not need an Account profile. Direct user permissions, user flags,
passwords and the groups' permission selections are unchanged.

Reading user groups requires `auth.view_user`. Both assignment endpoints require
`auth.view_user`, `auth.change_user`, `auth.view_group` and `account.manage_access`.
All use session authentication and permission checks. Non-superusers cannot
change their own role or a superuser's role.
Anonymous/inactive callers receive 403, as do PUT requests without valid CSRF.
The body-based assignment endpoint supports POST and OPTIONS; the user-specific
endpoint supports GET, PUT, HEAD and OPTIONS. Responses are not cached.

Updates are atomic and lock the target user on databases supporting row locking;
SQLite does not provide those row locks. Concurrent saves have no revision check.
No database migration is required.

## Log out

Send `POST /account/api/logout/` with the session cookie and current CSRF token.
The endpoint requires an authenticated session, clears it through Django's
`logout()`, and returns `{"message": "Logged out successfully."}`. GET requests
do not log out. The dashboard avatar dropdown submits this request through fetch
and returns the browser to the login page.

The Profile dropdown link opens `/account/profile/`, a protected read-only view
of the current user's account. No model migration is required, but Django's
sessions migration must be applied to the database.
