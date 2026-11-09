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

## Log out

Send `POST /account/api/logout/` with the session cookie and current CSRF token.
The endpoint requires an authenticated session, clears it through Django's
`logout()`, and returns `{"message": "Logged out successfully."}`. GET requests
do not log out. The dashboard avatar dropdown submits this request through fetch
and returns the browser to the login page.

The Profile dropdown link opens `/account/profile/`, a protected read-only view
of the current user's account. No model migration is required, but Django's
sessions migration must be applied to the database.
