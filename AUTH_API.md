# Session login API

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
`X-CSRFToken` header. Permission is `IsAuthenticated`: any authenticated user can
create a regular user for now; staff privileges are not required.

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
`IsAuthenticated`; any authenticated user can list users for now. It includes
active and inactive users, including users without an Account profile.

The response contains `count`, `next`, `previous`, and `results`, with 20 users
per page, newest first. Each result exposes only `id`, `full_name`, `email`,
`phone_number`, `designation`, `is_active`, and `date_joined`. Passwords, OTPs,
birth dates, addresses, and permissions are not included.

Use `?page=2` for later pages or `?q=avery` to search by name, email, phone or
designation. Search is case-insensitive. An empty search result returns HTTP 200
with an empty `results` array; an invalid page returns 404. Anonymous requests
return 403. This GET request does not require a CSRF header.

The protected `/account/add-user/` page calls the add-user API with fetch and
redirects to `/account/users/` on success. The list page fetches this endpoint
and supports search, pagination, loading, empty, and retry states. Both pages
are available under the expandable Users sidebar menu.

## List permissions

Send `GET /account/api/permissions/` with a logged-in session cookie. The endpoint
uses `SessionAuthentication` and `IsAuthenticated`, matching Add User. Any active
authenticated user can list permissions; no staff or superuser status is required.
GET requests do not need a CSRF header or request body.

The response is a JSON array of all Django permissions, including custom
permissions. Each item contains `id`, `name`, `codename`, `content_type` (the
content-type ID), `app_label`, and `model`. Results are ordered by app, model,
and codename. There is no pagination. This lists available permissions rather
than only permissions assigned to the current user, and does not assign any.
Anonymous requests return 403; POST is not supported.

## List groups

Send `GET /account/api/groups/` with a logged-in session cookie. Like Add User
and the permission list, it uses `SessionAuthentication` and `IsAuthenticated`.
No request body or CSRF header is needed for this GET request.

The response is a JSON array of all Django groups, ordered by name, without
pagination. Each item contains `id`, `name`, and `permissions` (a list of
permission IDs matching the permission-list endpoint). Groups without permissions
have an empty list; no groups returns `[]`. Group membership is not required to
list a group. This endpoint does not create groups or assign memberships or
permissions. Anonymous requests return 403; POST is not supported.

## Manage roles

Roles use Django groups. The protected `/account/roles/` page loads the group
list API and displays each role's name with Edit, Manage permissions and Delete
icon actions. The sidebar has an expandable Roles & Permissions section.

`GET /account/api/groups/<id>/` returns one group's ID, name and permission IDs.
`PATCH` on the same URL renames it with `{"name": "Teachers"}`. Names are
required, nonblank, unique and at most 150 characters. Permission IDs are read-only
on this endpoint. `DELETE` removes the group and returns 204 with no body; it
removes group memberships without deleting user accounts or permission records.
These actions use `SessionAuthentication` and `IsAuthenticated`; any authenticated
user can currently rename or delete roles. PATCH and DELETE require CSRF.
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

Authentication is `SessionAuthentication` and the current permission class is
`IsAuthenticated`: any active authenticated user can assign permissions.
Requests without a valid session/CSRF token receive 403. Only PUT and OPTIONS are supported;
read current assignments through the existing group-detail GET endpoint.

Replacement uses `transaction.atomic()` and locks the group with
`select_for_update()` on databases that support row locking. Assignment writers
should use the same locking convention. SQLite does not provide these row locks.
There is no revision check, so a later completed save can replace an earlier
selection. The permission page connects to this endpoint and redirects to
`/account/roles/` after a successful save.

## Log out

Send `POST /account/api/logout/` with the session cookie and current CSRF token.
The endpoint requires an authenticated session, clears it through Django's
`logout()`, and returns `{"message": "Logged out successfully."}`. GET requests
do not log out. The dashboard avatar dropdown submits this request through fetch
and returns the browser to the login page.

The Profile dropdown link opens `/account/profile/`, a protected read-only view
of the current user's account. No model migration is required, but Django's
sessions migration must be applied to the database.
