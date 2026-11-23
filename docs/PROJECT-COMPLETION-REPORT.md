# CareConnect Hospital Management System — Project Completion Report

## 1. Project overview

CareConnect is an existing Flask application for demonstrating common hospital workflows through one role-based web portal. Patients can register, maintain profiles, browse doctors, request appointments, review their own medical records, and view their own invoices. Doctors can manage assigned appointments and records they authored. Administrators manage accounts, doctors, appointments, invoices, directories, and audit history.

The application is intended for local demonstration and testing. It is not a production clinical system or a compliance certification.

## 2. Technologies used

- Python 3.13
- Flask 3.1.3
- Flask-Login 0.6.3
- Flask-SQLAlchemy 3.1.1 and SQLAlchemy 2.1.8
- SQLite by default; SQLAlchemy database URLs can be configured through `DATABASE_URL`
- Jinja templates, HTML, responsive CSS, and vanilla JavaScript
- `unittest` for automated tests
- Werkzeug password hashing and Flask/Werkzeug request handling

Pinned Python dependencies are listed in `requirements.txt`.

## 3. System architecture

- `app.py` provides the application factory, shared security hooks, CLI commands, error handlers, database initialization, and blueprint registration.
- `extensions.py` defines the shared SQLAlchemy and Flask-Login extensions.
- `routes/` contains the authentication, dashboard/directory, administration, appointment, medical-record, and billing blueprints.
- `models/__init__.py` defines the ORM models and Flask-Login user loader.
- `audit.py` centralizes audit event creation; `billing_service.py` centralizes invoice status transitions and overdue processing.
- `templates/` contains shared layouts and workflow pages; `static/css/style.css` and `static/js/app.js` provide responsive styling and navigation behavior.
- `tests/test_app.py` exercises route behavior, validation, authorization, workflows, and database behavior.

Requests pass through Flask routes and role/ownership checks, use SQLAlchemy models and services for changes, and render Jinja templates. State-changing requests are checked for a session CSRF token before their route handler runs.

## 4. Implemented modules

1. **Authentication and accounts:** patient registration, sign-in, sign-out, administrator-created accounts, and role-specific dashboards.
2. **Patient profiles and directory:** patient profile maintenance and an administrator-only patient directory.
3. **Doctor management and directory:** administrator creation/editing of doctor accounts and profiles; doctor listing for signed-in users.
4. **Appointments:** patient booking, duplicate time-slot prevention, role-specific status transitions, and patient/doctor visibility rules.
5. **Medical records:** authorized creation, editing, listing, and detail access with patient ownership and doctor authorship checks.
6. **Billing:** manual and appointment-linked invoices, status transitions, printable invoices, and patient-scoped demonstration payment simulations.
7. **Overdue invoice handling:** request-driven checks while billing pages are used and the `mark-overdue-invoices` CLI command.
8. **Administration:** account and doctor management, invoice creation, patient directory, and live dashboard statistics/activity.
9. **Audit history:** administrator-only display of the latest 200 events; event summaries are intended to omit passwords, clinical details, and payment credentials.
10. **Development utilities:** database initialization, interactive administrator creation, and opt-in synthetic doctor seeding.

## 5. Database design

The default database is `instance/careconnect.db`. The eight ORM tables are:

| Table | Purpose and relationships |
|---|---|
| `users` | Shared accounts and roles; one-to-one patient/doctor profiles; referenced by appointments, records, invoices, audit events, and demo payments. |
| `patient_profiles` | Optional patient-specific information, linked one-to-one to a user. |
| `doctor_profiles` | Doctor specialization, department, license, qualification, experience, availability, and fee; linked one-to-one to a user. |
| `appointments` | Patient, doctor, scheduled time, reason, status, and creation time; has a unique doctor/time constraint. |
| `medical_records` | Patient and authoring doctor, optional appointment, diagnosis, treatment, notes, and recorded time. |
| `invoices` | Patient, optional appointment, description, amount, status, issue time, and optional due date; appointment invoices are constrained against duplicates by an index. |
| `audit_events` | Optional actor ID and retained actor name, action, entity type/ID, safe summary, and event time; indexed for event time/entity lookup. |
| `demo_payment_simulations` | Synthetic payment reference and amount associated with an invoice and patient; not a real payment transaction. |

Foreign keys and uniqueness constraints enforce important relationships. SQLite initialization installs audit-event immutability triggers and safe additive/index migrations. The application initialization uses `create_all()` and explicit compatibility migrations; it is not a general-purpose schema migration framework.

## 6. Authentication and security

- Passwords are stored as Werkzeug-generated password hashes, not plaintext.
- Flask-Login protects authenticated routes; role checks and record ownership checks restrict access.
- State-changing forms require a session-bound CSRF token checked with a constant-time comparison.
- Login redirects reject external destinations.
- Session and remember cookies are HTTP-only and use `SameSite=Lax`; `SESSION_COOKIE_SECURE=1` enables the Secure flag for HTTPS.
- Dynamic responses use `Cache-Control: private, no-store`; responses add `nosniff`, frame, referrer, and permissions policy headers.
- Inputs are validated for fields such as email, phone, dates, roles, invoice amounts, and status transitions.
- Audit summaries do not include sensitive clinical/payment fields; payment is a simulation and accepts no card or bank credentials.

These are application safeguards, not a penetration test. The SQLite audit log is not an external tamper-proof logging service.

## 7. Testing results

The completed automated test run used:

```text
Ran 68 tests
OK
```

Tests ran with the project virtual environment. Test cases configure isolated in-memory SQLite databases; the module-level Flask application was also directed to a temporary SQLite database for the run, rather than the existing application database.

Coverage includes authentication and role access, registration and password hashing, doctor/user management, appointment booking and transitions, record access and updates, invoice validation and transitions, audit events and persistence, dashboard statistics, migration behavior, security headers, and database integrity. A separate static check enumerated 29 URL rules, compiled 23 templates, and found no unresolved literal template `url_for` endpoints.

Browser checks used synthetic accounts and a temporary SQLite database. The admin dashboard and key admin/workflow pages returned HTTP 200; an account-creation audit event remained visible after restarting Flask against the same temporary database. The layout and mobile navigation were observed at desktop and 390×844 mobile viewports. No screenshot files are currently stored in the project.

The existing local database was not used for browser data creation. During the QA session it had 4 user rows and 0 audit-event rows; SQLite integrity and foreign-key checks passed.

## 8. Screenshots required for demonstration

Capture screenshots with synthetic data only. Recommended set:

1. Public home page and patient registration or sign-in.
2. Patient dashboard and doctor directory.
3. Appointment booking and appointment status view.
4. Doctor dashboard and medical-record form/detail page using synthetic clinical text.
5. Administrator dashboard with statistics and navigation.
6. Doctor management and People/patient directories.
7. Billing list and a printable invoice using synthetic amounts.
8. Audit log showing a synthetic event and its safe summary.
9. Mobile navigation or a representative page at a narrow viewport.

Before sharing screenshots, verify that they contain no real patient information, personal accounts, passwords, session tokens, or credentials.

## 9. Known limitations

- The Flask development server is for local use, not production deployment.
- The payment flow is explicitly a demonstration; it does not process payments.
- Overdue invoice processing is request-driven or CLI-triggered, not an always-running scheduler.
- SQLite audit immutability triggers do not protect against privileged database-file access; logs are not sent to an external tamper-resistant sink.
- Schema compatibility is handled through app initialization and targeted additive/index migrations; there is no versioned migration history.
- The project is educational and does not provide medical advice or a compliance certification. Production use would require a dedicated security, privacy, operational, and compliance review.
- Screenshots are not currently included in the repository.

## 10. Installation and execution instructions

Commands below are for Windows PowerShell, run from the project directory. Keep the existing `instance/careconnect.db`; do not delete or recreate it.

### Install dependencies

```powershell
Set-Location C:\Users\Rakes\Hospital-Management-System
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Configure the application secret

Set a private, persistent secret key and reuse it across restarts. Do not commit the value:

```powershell
$env:SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
```

For deployments, store the generated key securely in the environment rather than generating a new value at every startup. For HTTPS deployments, set `$env:SESSION_COOKIE_SECURE = "1"`. Leave it unset for local HTTP.

### Initialize the database and create an administrator if needed

`init-db` creates missing tables and applies the application's safe initialization logic; it does not reset existing rows:

```powershell
flask --app app init-db
```

If the selected database has no administrator, create one interactively:

```powershell
flask --app app create-admin
```

### Start CareConnect

```powershell
flask --app app run --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000`.

### Run tests without targeting the existing database

The test cases use in-memory SQLite, but importing the application also creates its module-level Flask app. Point that app at a temporary database for the test process:

```powershell
$testDb = Join-Path $env:TEMP ("careconnect-tests-" + [guid]::NewGuid().ToString("N") + ".sqlite")
$env:DATABASE_URL = "sqlite:///" + $testDb.Replace("\", "/")
$env:SECRET_KEY = "isolated-test-only-secret"
python -m unittest discover -s tests -v
Remove-Item Env:\DATABASE_URL
Remove-Item Env:\SECRET_KEY
```

The unique temporary test database is separate from `instance/careconnect.db`.
