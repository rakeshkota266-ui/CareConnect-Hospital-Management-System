# CareConnect Hospital Management System

## Project summary

CareConnect is an existing Flask application that brings the patient, clinician, and hospital-administration workflows together in one responsive portal. Its purpose is to make common hospital coordination tasks easier to demonstrate and test: managing accounts, scheduling appointments, recording care, and tracking invoices. It uses role-based access and patient ownership checks to keep clinical and billing information scoped to authorized users.

### Modules and workflow

- **Authentication and accounts:** patients can register and sign in; administrators create staff accounts. Passwords are hashed, forms use session-bound CSRF tokens, and protected views require authentication and the appropriate role.
- **Patient management:** patients maintain contact and profile information. Administrators can review the patient directory; patients see only their own appointments, records, and invoices.
- **Doctor management:** administrators add and edit doctor accounts, qualifications, experience, availability, and consultation fees. Patients can browse the directory and select a doctor when requesting a visit.
- **Appointments:** patients request a future date and time. The application rejects invalid or duplicate doctor time slots. Doctors and administrators can update eligible statuses; patients can cancel their own eligible appointments.
- **Medical records:** doctors create and update records they authored. Administrators can manage records, and patients can view only their own.
- **Billing:** administrators create manual invoices or generate them from appointments using the configured doctor fee. Duplicate appointment invoices are prevented. Optional due dates are validated; unpaid invoices past their due date are automatically marked Overdue when billing is viewed, and can also be updated by the maintenance command. Paid and Cancelled invoices are never overwritten by overdue detection. Patients can view and print only their own invoices.
- **Payment demonstration:** patients can simulate settling their own Unpaid or Overdue invoice. The app stores a synthetic `DEMO-...` reference and changes the invoice status; it never asks for card/bank details and does not contact a payment provider or process real money.
- **Audit trail:** an administrator-only audit page records account/doctor administration, appointment bookings and status changes, medical-record creation/updates, invoice creation/status updates, overdue transitions, and demo payment simulations. Summaries omit diagnoses, treatment notes, passwords, and payment credentials. SQLite deployments reject edits/deletions of audit entries through database triggers.
- **Administration:** the dashboard summarizes live patient, doctor, appointment, and invoice counts and shows recent appointment, record, and billing activity.

Typical patient flow: register → sign in → maintain profile → choose a doctor → request an appointment → view appointment updates, medical records, and invoices. Typical staff flow: administrator creates doctor accounts and oversees operations; doctors review assigned appointments and create or update their authorized clinical records.

## Objectives

- Demonstrate a full-stack hospital-management workflow using Python, Flask, HTML, CSS, JavaScript, and SQLite.
- Apply authentication, authorization, validation, CSRF protection, and ownership checks to the main user workflows.
- Preserve existing account and clinical/billing data while extending database schema safely.
- Provide a responsive interface suitable for local project demonstrations.

## Features

- Patient registration, login, logout, and profile maintenance.
- Password changes from the signed-in profile page with current-password verification.
- Administrator and doctor sign-in with role-specific access.
- Doctor directory and administrator-managed doctor profiles.
- Appointment booking, conflict prevention, and status tracking.
- Patient-scoped medical records, with author checks for doctors.
- Manual and appointment-linked invoices, status tracking, and printable invoice copies.
- Automatic overdue checks on billing views plus `mark-overdue-invoices` for scheduled maintenance.
- Patient-scoped demonstration payment simulations; no real payment processing.
- Admin-only audit history for key workflow changes.
- Database-backed administrator statistics and recent activity.
- Development-only synthetic doctor seeding, disabled unless explicitly enabled.

## Technology stack

- Python 3.13 (the included virtual environment is configured for Python 3.13)
- Flask 3.1, Flask-Login, Flask-SQLAlchemy, and SQLAlchemy
- SQLite by default; SQLAlchemy database URLs can be supplied through `DATABASE_URL`
- Jinja templates, responsive CSS, and vanilla JavaScript
- `unittest` for automated route, validation, authorization, and persistence tests

## Project structure

```text
CareConnect/
├── app.py                     Flask application, configuration, CLI, and shared security
├── extensions.py              SQLAlchemy and Flask-Login extensions
├── validators.py              Shared email and phone validation
├── audit.py                   Shared audit-event writer
├── billing_service.py         Due-date refresh and invoice transition rules
├── models/
│   └── __init__.py            User, patient, doctor, appointment, record, invoice, audit, and demo-payment models
├── routes/
│   ├── auth.py                Registration, login, and logout
│   ├── main.py                Dashboards, profiles, and directories
│   ├── admin.py               Administration, doctors, accounts, and invoice creation
│   ├── appointments.py        Booking, listing, and status transitions
│   ├── records.py             Medical record access and management
│   └── billing.py             Invoice listing, status management, demo payment, and printing
├── templates/                 Jinja pages and role-specific interfaces
│   ├── admin/audit.html        Administrator audit history
│   ├── invoice_print.html      Printable invoice copy
│   └── payment_confirmation.html  Payment simulation result
├── static/
│   ├── css/style.css          Responsive and print styles
│   └── js/app.js              Navigation and small interface interactions
├── tests/test_app.py          Automated application tests
├── instance/                  Local database and uploads (created at runtime; not committed)
├── requirements.txt           Python dependencies
└── README.md
```

## Installation and environment setup (Windows PowerShell)

Run commands from the project folder. The `instance/` database and uploaded files are local runtime data and are excluded from Git.

```powershell
Set-Location C:\Users\Rakes\Hospital-Management-System
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set a private, persistent secret key for the current shell. Use the same value between restarts; do not commit it:

```powershell
$env:SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
```

By default, the application uses `instance\careconnect.db`. To use another SQLAlchemy database, set `DATABASE_URL` before starting the application. For HTTPS deployments, set `SESSION_COOKIE_SECURE=1`; local HTTP development should leave it unset.

## Database initialization and administrator setup

The `init-db` command creates missing tables and applies safe additive/index migrations; it does not reset or delete existing rows:

```powershell
flask --app app init-db
```

If the database does not already have an administrator, create one interactively. The command prompts for name, email, and password and does not ship a default account:

```powershell
flask --app app create-admin
```

To add synthetic doctors on a development database only:

```powershell
$env:CARECONNECT_ALLOW_DEMO_SEED = "1"
flask --app app seed-doctors --count 3
Remove-Item Env:\CARECONNECT_ALLOW_DEMO_SEED
```

The seed command is disabled unless explicitly opted into and uses `@careconnect.test` accounts with generated passwords. Do not use seed data in a live or production database.

Billing lists and invoice views refresh overdue statuses on access. To run overdue processing independently (for example, using Windows Task Scheduler), invoke this command from the project directory with the usual database settings:

```powershell
flask --app app mark-overdue-invoices
```

## Run the application

```powershell
flask --app app run --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000`. The Flask development server is intended for local demonstrations only; use a production WSGI server and HTTPS for deployment. No cloud deployment is performed by this project.

## AWS deployment

The [AWS deployment guide](AWS-DEPLOYMENT-GUIDE.md) documents Ubuntu Server 24.04, Gunicorn, Nginx, systemd, HTTPS, persistent database/uploads, environment configuration, and backups. It is an operator guide only; no AWS resources or live servers are provisioned by this repository.

## Testing

Activate the project virtual environment, then run the full suite:

```powershell
python -m unittest discover -s tests -v
```

The verified suite contains 68 tests and passes with no failures or errors.

The tests use isolated in-memory SQLite databases and synthetic accounts. They cover authentication, registration, role authorization, patient/doctor management, appointments and transitions, medical records, invoice totals/status/printing access, due-date transitions, audit events, payment simulations, validation, and database integrity. Do not point test runs at a database containing real patient data.

## Screenshots

No screenshots are currently stored in the repository. Add locally captured, synthetic-data-only screenshots here before project submission, for example:

- Patient dashboard and appointment flow
- Doctor appointment and medical-record workflow
- Administrator dashboard and doctor management
- Billing list and printable invoice

Do not capture real patient information, credentials, or personal account details.

## Project documentation

- [Installation guide](INSTALLATION-GUIDE.md)
- [Architecture](PROJECT-ARCHITECTURE.md)
- [Security report](SECURITY-REPORT.md)
- [Testing report](TESTING-REPORT.md)
- [AWS deployment guide](AWS-DEPLOYMENT-GUIDE.md)
- [B.Tech viva preparation](docs/CARECONNECT-VIVA-PREPARATION.md)
- [Project completion report](docs/PROJECT-COMPLETION-REPORT.md)

## Future enhancements

- Add automated browser tests and deployment-specific configuration checks.
- Add appointment availability calendars and notifications.
- Expand audit coverage to include clinical-record access events and add a secured external log sink.
- Complete a privacy, accessibility, and production-readiness review before handling real patient information.

## Security and project limitations

CareConnect includes hashed passwords, role and ownership checks, CSRF validation, secure cookie options, no-store headers on dynamic pages, and a SQLite audit log. Configure a persistent secret key and HTTPS for any shared deployment. Overdue processing is request-driven or CLI-triggered rather than backed by an always-running scheduler. The payment flow is a demonstration only, not payment processing. The audit trail is not an external tamper-proof log, and SQLite immutability protections do not replace secured backups or database access controls. This educational project does not provide medical advice or a compliance certification; use synthetic data for demonstrations and testing.
