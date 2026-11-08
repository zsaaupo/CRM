# Signup verification email

Signup creates an inactive user and an account. It generates a six-character code
using `0-9` and `A-Z` with Python's `secrets` module, with at least one digit and one
letter. The current signup stores the code in `Account.otp`, alongside
`otp_expires_at` (ten minutes after creation). These fields are
read-only in admin and omitted from account API responses. The verifier also
supports codes issued with Django password hashing by the earlier implementation.

The email thread starts through `transaction.on_commit()`, so a rolled-back signup
does not send an email. The thread logs failures; it has no automatic retries and
may be interrupted if the web process exits. Use a durable background worker for
production email delivery and retries.

## Gmail configuration

The project automatically loads `.env` from the folder containing `manage.py`.
The local file is excluded from Git; `.env.example` contains safe placeholders.
For a new checkout, install the dependencies and copy the example:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Replace the example `SECRET_KEY` with a unique random Django secret. Your current
local `.env` already preserves the existing project secret.

Edit these entries in your local `.env`:

```dotenv
EMAIL_HOST_USER=your-address@gmail.com
EMAIL_HOST_PASSWORD=your-google-app-password
```

Restart the Django server after editing the file. Existing environment variables
from your terminal, IDE, or deployment take priority over `.env` values.

An eligible Google account with 2-Step Verification can use an app password:
https://support.google.com/accounts/answer/185833

When both variables are set, Django defaults to Gmail SMTP with SSL on port 465 and
a ten-second timeout. `.env` also configures `EMAIL_HOST`, `EMAIL_PORT`,
`EMAIL_USE_SSL`, `EMAIL_USE_TLS`, and `EMAIL_TIMEOUT`. Enable only one of SSL or TLS:
Gmail uses SSL on port 465 or STARTTLS on port 587. `DEFAULT_FROM_EMAIL` can
optionally override the sender.
When either credential is absent, the development console backend prints email
content in the server terminal instead of sending it. Never commit credentials.

## Verify an OTP

Send a JSON POST to `/account/api/verify-otp/`:

```json
{
  "email": "person@example.com",
  "otp": "A0Z9B1"
}
```

Use the actual code from the signup email. A valid unexpired code activates the
user, clears the code and expiry, and returns HTTP 200. Unknown
accounts, wrong/expired codes, and reused codes receive the same HTTP 400 error.
Wrong submissions do not invalidate the code; it remains usable until expiry or
successful verification. Activation and code consumption run in one database
transaction. This endpoint does not log in the user.

## Request a new OTP

Send a JSON POST to `/account/api/resend-otp/`:

```json
{
  "email": "person@example.com"
}
```

The email must identify an inactive user and its linked account. The endpoint
replaces the previous code, gives the new code a ten-minute expiry, and starts the
email thread only after the transaction commits. It returns HTTP 200 without
returning the code. The old code stops working immediately. Expired or missing
codes can be replaced through this endpoint as well.

Malformed emails return HTTP 400 field errors. Unknown emails, users without an
account, and already-active users receive HTTP 400 with
`No pending verification found for this email.` The endpoint does not create a
user or activate one; verification is still required.

A read-only field prevents form/API edits; trusted server code can still update it.
