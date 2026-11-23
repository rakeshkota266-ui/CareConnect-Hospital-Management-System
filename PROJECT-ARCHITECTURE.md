# CareConnect Project Architecture

## Architecture overview

CareConnect uses a server-rendered Flask application with blueprints, SQLAlchemy, Jinja, and a relational database.

```text
Browser
  -> Nginx / Flask development server
  -> Flask application factory (app.py)
  -> Authentication and role decorators
  -> Route blueprints
  -> SQLAlchemy models and services
  -> SQLite or configured SQLAlchemy database
  -> Jinja templates and static assets
```

## Application components

- `app.py`: application factory, configuration, security headers, CSRF validation, CLI commands, migrations, health check, and error handlers.
- `extensions.py`: shared SQLAlchemy and Flask-Login extension instances.
- `models/__init__.py`: users, patient profiles, doctor profiles, appointments, medical records, attachments, invoices, audit events, and demo payments.
- `routes/`: authentication, dashboards/directories, administration, appointments, records, and billing.
- `audit.py`: centralized audit-event creation.
- `billing_service.py`: invoice transitions and overdue processing.
- `utils/decorators.py`: login and role-based authorization.
- `utils/uploads.py`: secure attachment validation and preparation.
- `templates/`: Jinja HTML pages.
- `static/`: responsive CSS and vanilla JavaScript.
- `tests/test_app.py`: isolated route, validation, authorization, persistence, and integrity tests.

## Request flow

1. Nginx or the Flask development server receives the request.
2. Flask applies session and CSRF checks to state-changing methods.
3. The blueprint performs authentication and role checks.
4. Routes validate form values and ownership.
5. SQLAlchemy commits or rolls back the database transaction.
6. Jinja renders the response.

## Database relationships

- `User` has optional one-to-one `PatientProfile` or `DoctorProfile`.
- `Appointment` belongs to one patient and one doctor.
- `MedicalRecord` belongs to one patient and one authoring doctor and may reference an appointment.
- `MedicalRecordAttachment` belongs to one medical record.
- `Invoice` belongs to one patient and may reference one appointment.
- `DemoPaymentSimulation` belongs to one invoice and one patient.
- `AuditEvent` optionally references an actor user.

## Security boundaries

- Patients can access only their own appointments, records, and invoices.
- Doctors can manage only appointments assigned to them and records they authored.
- Administrators manage accounts, doctor profiles, billing, and audit history.
- Administrators cannot create medical records through the normal clinical workflow.
- State-changing forms require CSRF tokens.
- Passwords use Werkzeug password hashes.

## Deployment architecture

```text
Internet -> HTTPS/80/443 Nginx -> Gunicorn 127.0.0.1:8000 -> Flask
                                         |
                                         -> /var/lib/careconnect/careconnect.db
                                         -> /var/lib/careconnect/uploads
```

Gunicorn is a WSGI server and Nginx is the public reverse proxy. The SQLite database and attachment directory should be outside the application source tree and included in backup procedures.
