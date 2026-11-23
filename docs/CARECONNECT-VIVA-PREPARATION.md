# CareConnect Hospital Management System — B.Tech Viva Preparation

This guide is based on the current source code and
[`PROJECT-COMPLETION-REPORT.md`](./PROJECT-COMPLETION-REPORT.md). It describes
implemented behavior only. CareConnect is an educational/local demonstration,
not a production clinical system or compliance certification.

## 1. Complete project explanation

CareConnect is a role-based web application for demonstrating routine hospital
coordination workflows. It provides separate experiences for patients,
doctors, and administrators through one web portal.

- Patients can register and sign in, manage a profile, browse the care team,
  request appointments, review their own medical records, and view their own
  invoices. The payment feature is explicitly a demonstration simulation.
- Doctors can view their assigned appointments, take allowed appointment
  status actions, and create or update records they authored.
- Administrators can manage accounts and doctor profiles, review directories,
  view dashboard statistics, create invoices, and inspect the latest 200 audit
  events.

The browser sends HTTP requests to Flask. Route handlers check the user's
identity, role, and (where needed) ownership of a record. SQLAlchemy maps
Python model objects to database tables. The response is rendered with Jinja
templates and styled with responsive CSS and JavaScript.

SQLite is the default database. Application startup creates missing tables and
applies targeted compatibility changes. It does not provide a general
versioned migration history. Tests use isolated in-memory SQLite databases;
the documented full test run completed with 56 tests passing.

### A short presentation answer

> CareConnect is a Flask-based, role-controlled hospital workflow
> demonstration. Patients, doctors, and administrators use it for accounts,
> appointments, medical records, billing, and administration. SQLAlchemy
> connects the Python application to SQLite, while Jinja, HTML, CSS, and
> JavaScript provide the web interface. The application checks roles and
> record ownership, validates workflow transitions, and includes an
> administrator audit history. It is an educational demonstration, not a
> production medical or payment system.

## 2. Architecture explanation

### Request flow

1. A user opens a page or submits a form in a browser.
2. Flask matches the URL and HTTP method to a route in a registered blueprint.
3. Login, role, CSRF, input-validation, and record-ownership checks run as
   appropriate.
4. Route code calls SQLAlchemy models or a service such as
   `billing_service.py`.
5. SQLAlchemy reads or writes the configured database.
6. Flask renders a Jinja template or redirects to another route.
7. Response headers add cache and browser security controls.

### Main code layers

- **`app.py`** — application factory, configuration, blueprint registration,
  CSRF guard, template values, response headers, error handlers, CLI commands,
  database creation, and targeted compatibility updates.
- **`extensions.py`** — shared SQLAlchemy and Flask-Login extension objects.
- **`routes/`** — authentication, dashboard/directory, administration,
  appointments, records, and billing HTTP endpoints.
- **`models/__init__.py`** — the eight ORM models, relationships, and
  Flask-Login user loader.
- **`audit.py`** — creates audit-event objects in the active SQLAlchemy
  transaction.
- **`billing_service.py`** — invoice status transition rules and
  request/command-driven overdue processing.
- **`templates/`** — Jinja pages, including shared navigation and pages for
  individual workflows.
- **`static/css/style.css` and `static/js/app.js`** — responsive presentation
  and client-side navigation behavior.
- **`tests/test_app.py`** — automated route, validation, authorization,
  workflow, migration, and persistence tests.

The application factory makes it possible to configure a separate app for
tests. The test case creates an in-memory SQLite database and tears it down
after each test.

## 3. Technology explanations

### Python

Python is the server-side programming language. It implements route behavior,
validation, permissions, model definitions, and service logic. Its decimal
arithmetic is used for invoice amounts rather than relying on binary floating
point for currency values.

### Flask

Flask is the web framework. It maps URLs and HTTP methods to Python functions,
provides request and session handling, renders templates, supports
application-level hooks and error handlers, and hosts the CLI commands.
CareConnect uses Flask blueprints to organize endpoints by feature.

### Flask-Login

Flask-Login manages the signed-in user session and provides
`current_user`/`login_required` behavior. CareConnect adds `roles_required` to
require an allowed normalized role on restricted endpoints. Ownership checks
are separate: being logged in is not enough to read another patient's record.

### SQLAlchemy and Flask-SQLAlchemy

SQLAlchemy maps Python classes to relational tables and supplies queries,
relationships, constraints, and transactions. Flask-SQLAlchemy integrates the
ORM session with the Flask application context. The source uses
`db.session.commit()` to persist successful changes and rolls back database
integrity failures where handled.

### SQLite

SQLite is the default persistent relational database. It stores users,
profiles, appointments, medical records, invoices, audit events, and demo
payment simulations. A SQLAlchemy `DATABASE_URL` can select another supported
database configuration. Tests use in-memory SQLite; persistence testing also
uses a temporary file-backed SQLite database.

### Frontend

- **HTML** defines page structure and forms.
- **Jinja** fills templates with server-side data and shared layout elements.
- **CSS** styles the pages and supplies responsive layouts.
- **Vanilla JavaScript** supports client-side interface behavior such as
  navigation.

It is a server-rendered application; the project does not claim to be a
separate React/Vue frontend or a REST-only API.

### Supporting components

- **Werkzeug security utilities** generate and check password hashes.
- **`unittest`** is used for the automated test suite.
- **Click**, exposed through Flask's CLI, supports initialization,
  administrator creation, overdue invoice processing, and opt-in synthetic
  doctor seeding.

## 4. Database tables and relationships

The current ORM defines eight tables:

| Table | What it stores | Key relationships / rules |
|---|---|---|
| `users` | Shared account identity, email, password hash, role, phone, and creation time. | One-to-one optional patient and doctor profiles. Referenced by appointments, records, invoices, audit events, and demo payments. Email is unique; startup adds a case-insensitive unique index when existing data permits it. |
| `patient_profiles` | Optional patient details such as date of birth, gender, blood group, address, and emergency contact. | `user_id` is a unique foreign key to `users`, giving at most one profile per account. |
| `doctor_profiles` | Specialization, department, license, qualification, experience, fee, and availability. | `user_id` is a unique foreign key to `users`; license number is unique. |
| `appointments` | Patient, doctor, schedule, reason, status, and creation time. | Two foreign keys to `users` represent patient and doctor. A unique doctor/time constraint prevents two appointments for the same doctor at the same time. |
| `medical_records` | Patient, authoring doctor, optional appointment, diagnosis, treatment, notes, and recorded time. | Foreign keys point to `users` for patient and doctor, and optionally to `appointments`. |
| `invoices` | Patient, optional appointment, description, amount, status, issue time, and due date. | Foreign keys point to patient and optionally appointment. A unique index prevents more than one appointment-linked invoice for an appointment. |
| `audit_events` | Optional actor ID, retained actor name, action, entity type/ID, safe summary, and event time. | Optional actor foreign key to `users`; indexed by event time and entity. SQLite triggers prevent deletion and protect event content from updates. |
| `demo_payment_simulations` | Synthetic reference, amount, patient, invoice, and creation time. | One-to-one with an invoice; the invoice ID is unique. This is not a real payment record. |

### Relationship summary

- One user account may have one patient profile or one doctor profile, depending
  on its role.
- A patient may have many appointments and invoices.
- A doctor may have many appointments and authored medical records.
- A medical record belongs to a patient and a doctor, and may refer to an
  appointment.
- An appointment may be linked to an invoice; the schema prevents duplicate
  invoices for the same appointment.
- An invoice may have one demo payment simulation.
- An audit event may refer to a user actor, but the actor ID is nullable so
  system-created events can be recorded without a signed-in actor.

The default database file is `instance/careconnect.db`. Startup runs
`create_all()` and explicit additive/index compatibility logic. This is not a
replacement for a versioned migration system.

## 5. Explanation of every project module

### Authentication and account management

The public routes support patient registration and login. Registration checks
the name, email, phone, password length (at least ten characters), confirmation,
and duplicate email. It creates a patient account and profile. Passwords are
stored as Werkzeug-generated hashes. Login checks the hash and accepts a
remember-me choice. Logout is a POST route protected by the CSRF guard.

Administrators can create patient, doctor, and administrator accounts. Initial
administrator creation is available through the interactive `create-admin`
CLI command; a default password is not shipped.

### Profiles

The signed-in user can update common profile fields. Patient profile input
includes date and selected personal details; doctor profile editing includes
contact information and availability. Validation checks permitted values,
lengths, and dates.

### Dashboards

The dashboard chooses its statistics and appointments based on the signed-in
user's role. The administrator dashboard reports database-derived counts and
recent activity. Patient and doctor dashboards show role-scoped information.

### Doctor directory and management

Signed-in users can browse doctors. Administrators can add and edit doctors
with a linked doctor profile. Inputs such as email, license, experience, and
consultation fee are validated. Doctor accounts cannot be casually converted
to another role because the application preserves their clinical history.

### Patient directory

The patient directory is restricted to administrators. It lists patient
accounts; patient-specific records and invoices are separately protected by
ownership checks.

### Appointments

Patients can request appointments with a doctor. The application validates
the doctor, reason, and future schedule. It checks for a duplicate doctor/time
slot in application logic and also enforces a database uniqueness constraint.

Allowed appointment states are `scheduled`, `confirmed`, `rejected`,
`completed`, and `cancelled`. Transition and role rules restrict who may make
each change. Doctors only manage their appointments, patients only manage
their own, and administrators can perform the allowed administrative
transitions. Invalid transitions are rejected rather than silently applied.

### Medical records

Doctors and administrators can create records. Doctors can edit records they
authored; administrators can manage records. Patients can view only their own
records, and doctors can view records they authored. Summaries in the audit
log do not include the clinical diagnosis, treatment, or notes.

### Billing and invoice management

Administrators can create manual invoices or appointment-linked invoices. An
appointment invoice uses the linked doctor's configured consultation fee and
patient, and duplicate appointment invoices are prevented. Amounts must be
positive and within the supported decimal precision and range.

Patients can view only their own invoices. Administrators manage invoice
statuses. The invoice transition rules are centralized in
`billing_service.py`. The application can mark eligible unpaid invoices
overdue when billing pages are used or through the `mark-overdue-invoices`
CLI command; there is no always-running scheduler.

The patient payment route creates a synthetic payment simulation and marks an
eligible invoice paid. It accepts no card or bank credentials and processes no
real payment.

### Audit history

Important actions add audit events in the same SQLAlchemy transaction as the
business change. Events capture an action, entity type and ID, timestamp,
available actor identity, and a safe summary. The administrator-only page
shows the newest 200 events in descending time order. SQLite triggers protect
events against ordinary update/deletion through SQL, but a user with
privileged access to the database file can still tamper with it; it is not an
external tamper-proof logging service.

### Development and operational utilities

- `init-db` creates missing tables and applies the application initialization
  behavior.
- `create-admin` interactively creates an administrator after validating
  details.
- `mark-overdue-invoices` processes overdue invoices on demand.
- `seed-doctors` creates synthetic doctors only when the explicit
  `CARECONNECT_ALLOW_DEMO_SEED=1` opt-in is set. Existing demo accounts are
  left unchanged.

## 6. Security features and boundaries

### Implemented controls

- Passwords are stored as hashes, not plaintext.
- Flask-Login protects authenticated routes.
- Role checks restrict administrator, doctor, and patient functions.
- Ownership checks restrict patient data and doctor-authored records.
- A session-bound CSRF token is required for POST, PUT, PATCH, and DELETE
  requests. Comparison uses `secrets.compare_digest`.
- Login rejects external redirect targets.
- Session and remember cookies are HTTP-only and use `SameSite=Lax`.
- The `SESSION_COOKIE_SECURE=1` setting enables the Secure cookie flag for
  HTTPS.
- Non-static responses use `Cache-Control: private, no-store`.
- Responses set `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, and `Permissions-Policy`.
- Input checks cover fields including email, phone, dates, roles, invoice
  amounts, and status transitions.
- Audit summaries are designed not to store passwords, clinical details, or
  payment credentials.
- There are no real payment credentials in the demonstration payment flow.

### Important limitations to acknowledge

- These controls do not establish that the application is production-ready.
- The default development server is for local development.
- SQLite audit immutability triggers do not protect the database file from a
  privileged operator.
- The project is not a compliance certification, penetration-test report, or
  medical-advice system.
- If `SECRET_KEY` is not configured, `app.py` generates a new random key at
  startup. For a stable deployment or repeatable sessions, configure a
  persistent secret outside source control.

## 7. Testing results

The completion report records this full automated run:

```text
Ran 56 tests in 47.326s
OK
```

The report says tests cover authentication and role access, registration and
password hashing, doctor/user management, appointment booking and transitions,
record access and updates, invoice validation and transitions, audit events
and persistence, dashboard statistics, migration behavior, security headers,
and database integrity.

Other recorded checks:

- 29 URL rules were enumerated.
- 23 templates compiled.
- No unresolved literal template `url_for` endpoints were found.
- Synthetic-data browser checks exercised the admin dashboard and workflow
  pages at desktop and mobile sizes.
- SQLite integrity and foreign-key checks passed.
- A synthetic audit event remained visible after restarting Flask against the
  same temporary database.

These are the report's recorded results; do not describe them as a new test
run performed during a viva.

## 8. Challenges and solutions reflected in the implementation

The repository does not contain a diary of the development process. The
following are defensible implementation challenges visible in the code, not
claims about undocumented personal experiences:

1. **Separating permissions by role and record ownership.** A generic login
   check would still allow users to access other users' data. The application
   combines role decorators with patient/doctor ownership checks in routes.
2. **Preventing invalid state changes.** Appointment and invoice statuses have
   explicit transition maps; the routes reject unsupported transitions.
3. **Avoiding conflicting appointment bookings.** The route checks for an
   existing slot, and a database uniqueness constraint also protects the
   doctor/time pair if concurrent requests race.
4. **Keeping audit information safe.** Audit summaries describe the action
   without copying medical details, passwords, or payment credentials.
   Events are saved with the related transaction.
5. **Protecting existing SQLite data when schema changes are needed.** The
   application uses `create_all()` plus targeted additive/index changes and
   avoids resetting tables. It logs when a constraint cannot be added safely
   because pre-existing duplicates exist.
6. **Testing without touching the application database.** Automated tests use
   in-memory SQLite; file-backed persistence tests use a temporary database.
7. **Avoiding a false claim of payment integration.** The payment feature is
   represented as a synthetic simulation and explicitly records that no real
   payment was processed.

When answering a question about a challenge, explain the specific code
approach and its limitation instead of claiming an undocumented incident.

## 9. Future enhancements

These are possible future improvements, not features currently implemented:

- Deploy through a production WSGI server with hardened environment settings.
- Add versioned schema migrations.
- Add scheduled background processing for overdue invoices.
- Integrate a real payment provider only if required, using its secure
  hosted/tokenized workflow rather than collecting card data directly.
- Send audit records to an external protected logging system.
- Complete dedicated security, privacy, operational, and compliance reviews
  before any real clinical use.
- Expand monitoring, backup, recovery, and deployment practices for a
  production environment.

## 10. Ten-minute live project demonstration script

### Before the viva

- Start CareConnect with a configured, persistent `SECRET_KEY`.
- Use a separate demo database containing only synthetic accounts and
  synthetic records. Do not demonstrate with real patient information.
- Prepare synthetic patient, doctor, and administrator accounts in advance.
  Do not assume demo accounts exist unless you created them in that isolated
  database.
- Keep the presentation/test report available. The project is a local demo;
  do not call it a production system.
- To run locally, follow the Windows instructions in
  [`PROJECT-COMPLETION-REPORT.md`](./PROJECT-COMPLETION-REPORT.md#10-installation-and-execution-instructions).

### Timed walkthrough

**0:00–0:45 — Introduce the project**

Say: “CareConnect is a role-based Flask hospital-workflow demonstration. It
has patient, doctor, and administrator experiences. I will use synthetic
data, and the payment workflow is a simulation only.”

Open the landing page and point out the sign-in and registration entry points.

**0:45–1:45 — Patient registration and login**

If prepared safely, show the registration form and briefly explain duplicate
email validation, required fields, password confirmation, minimum password
length, and hashed storage. Avoid creating an account during the viva if that
would pollute a shared or existing database. Sign in with the prepared demo
patient account.

**1:45–2:30 — Patient dashboard and profile**

Show the patient-specific dashboard and profile. Explain that dashboard
statistics are derived from data scoped to the logged-in user.

**2:30–3:20 — Care team and appointment booking**

Open the doctor list, then the appointment page. Explain the future-time
validation, doctor/time duplicate protection, and initial `scheduled` status.
If you book a live appointment, use the isolated demo database and a
pre-approved synthetic slot.

**3:20–4:20 — Doctor workflow**

Sign out and sign in as the prepared demo doctor. Show the doctor-specific
appointment list and explain the allowed status transition rules. If a
prepared synthetic appointment exists, show a valid status change; do not
attempt an invalid transition as a live experiment unless you are prepared to
show the expected error.

**4:20–5:20 — Medical record**

Show a pre-created synthetic medical record and explain that patients can
view only their own records, while doctors can access records they authored.
Use clearly fictitious clinical text. Do not expose real clinical data.

**5:20–6:35 — Administrator dashboard and directories**

Sign in as the prepared administrator. Show statistics, doctor management,
People, and the patient directory. Explain that these pages are restricted to
the administrator role. If adding a doctor live, use synthetic details and
the isolated database.

**6:35–7:35 — Billing**

Show a synthetic invoice and its status. Explain manual versus
appointment-linked invoices, decimal amount validation, and status
transitions. State explicitly: “The patient payment action records a demo
simulation; it does not contact a payment provider or process money.”

**7:35–8:25 — Audit log**

Open `/admin/audit`. Explain that it is administrator-only, displays the
latest 200 events in descending order, and uses safe summaries rather than
clinical details or payment credentials.

**8:25–9:15 — Architecture and database**

Explain the browser → Flask route/authorization → SQLAlchemy model/service →
SQLite → Jinja response flow. Name the eight tables: users,
patient_profiles, doctor_profiles, appointments, medical_records, invoices,
audit_events, and demo_payment_simulations.

**9:15–10:00 — Testing and close**

State the recorded result accurately: “The project completion report records
56 automated tests passing in 47.326 seconds. It also records template/route
checks and synthetic browser checks. These are development verification
results, not a security certification.”

Conclude with the system's scope and limitations: local educational
demonstration, no real payments, and no production/compliance claim.

### Optional terminal demonstration

If the examiner requests test execution, use an isolated database as described
in the completion report before running:

```powershell
python -m unittest discover -s tests -v
```

Do not point a test process or demo server at the existing application
database when synthetic writes are involved.

## 11. Fifty viva questions with answers

### Project and architecture

1. **What is CareConnect?**<br>
   It is a Flask-based, role-controlled web application demonstrating common
   hospital coordination workflows for patients, doctors, and administrators.

2. **What problem does the project address?**<br>
   It demonstrates how related account, appointment, record, billing, and
   administrative workflows can be presented in one portal with access
   restrictions.

3. **Who are the system's users?**<br>
   Patients, doctors, and administrators. Routes and templates present
   different permissions and data according to role.

4. **What is the main request flow?**<br>
   The browser sends a request; Flask routes it to a handler; permissions and
   validation run; SQLAlchemy accesses the database; Jinja renders the
   response.

5. **What does the application factory do?**<br>
   `create_app()` constructs and configures a Flask application, initializes
   shared extensions, registers blueprints, installs hooks and handlers, and
   initializes the database.

6. **Why are blueprints used?**<br>
   They group route handlers by feature—authentication, administration,
   appointments, records, billing, and common pages—rather than placing every
   route in one large module.

7. **Is the frontend a single-page application?**<br>
   No. It is server-rendered with Flask and Jinja, using HTML, responsive CSS,
   and vanilla JavaScript.

8. **What is the purpose of Jinja?**<br>
   Jinja inserts server-side values into HTML templates and supports shared
   layouts and conditional navigation.

### Technologies and database

9. **Why is Python used?**<br>
   It is the server-side language implementing route logic, validation,
   permissions, database models, and services.

10. **What does Flask provide?**<br>
    URL routing, request handling, sessions, template rendering, application
    hooks, error handling, and CLI integration.

11. **What is an ORM?**<br>
    An object-relational mapper connects program objects to relational
    database tables. SQLAlchemy lets the application work with model objects
    and relationships.

12. **Which database is used?**<br>
    SQLite is the default. SQLAlchemy database configuration is taken from
    `DATABASE_URL` when supplied.

13. **How many ORM tables are there?**<br>
    Eight: users, patient profiles, doctor profiles, appointments, medical
    records, invoices, audit events, and demo payment simulations.

14. **Why is there a separate patient profile table?**<br>
    It keeps patient-specific fields separate from shared account identity and
    role fields; its unique user foreign key allows at most one profile per
    account.

15. **How is a doctor profile related to a user?**<br>
    `doctor_profiles.user_id` is a unique foreign key to `users`, and the ORM
    maps it as a one-to-one relationship.

16. **How does an appointment reference its participants?**<br>
    It has separate `patient_id` and `doctor_id` foreign keys to `users`.

17. **How does the system prevent two doctor appointments at the same time?**<br>
    It checks the slot in the route and also has a database uniqueness
    constraint on doctor ID and scheduled time.

18. **How are medical records linked?**<br>
    Each record references a patient and the doctor who authored it; an
    appointment reference is optional.

19. **How are appointment invoices constrained?**<br>
    An invoice can optionally reference an appointment, and a unique index
    prevents multiple invoices for the same non-null appointment ID.

20. **How is a payment simulation connected to an invoice?**<br>
    It has a unique invoice foreign key, so an invoice can have at most one
    simulation record.

### Modules and workflows

21. **What can a patient do?**<br>
    Register, sign in, maintain a profile, browse doctors, request
    appointments, view their own records and invoices, and use the explicitly
    simulated payment workflow.

22. **What can a doctor do?**<br>
    View assigned appointments, perform allowed appointment transitions, and
    create or edit records they authored.

23. **What can an administrator do?**<br>
    Manage accounts and doctors, see directories and dashboard statistics,
    create invoices, and review audit history.

24. **What appointment statuses are defined?**<br>
    `scheduled`, `confirmed`, `rejected`, `completed`, and `cancelled`.

25. **How are appointment transitions controlled?**<br>
    A status-transition map and role-transition map define permitted changes.
    The route checks both and rejects invalid transitions.

26. **Can a patient view any medical record by changing its URL?**<br>
    No. The record detail route checks that the record's patient ID matches
    the signed-in patient. Doctors are similarly checked against the
    authoring doctor ID.

27. **How are invoices created?**<br>
    Administrators can create manual invoices with a patient, description,
    amount, and optional due date, or appointment-linked invoices using the
    linked patient and doctor's consultation fee.

28. **How are invoice amounts validated?**<br>
    The code uses `Decimal` and checks finiteness, positive/range limits, and
    at most two decimal places.

29. **How are overdue invoices processed?**<br>
    Eligible unpaid invoices are marked overdue when billing pages refresh
    overdue state or when the `mark-overdue-invoices` CLI command is run.
    There is no always-running scheduler.

30. **Does CareConnect accept credit card information?**<br>
    No. The patient payment feature creates a demonstration simulation and
    does not process a real payment or accept card/bank credentials.

### Security and operations

31. **How are passwords stored?**<br>
    `User.set_password()` uses Werkzeug's password-hash generation; login
    verifies the hash rather than comparing plaintext passwords.

32. **How is authentication implemented?**<br>
    Flask-Login tracks the signed-in user and protects authenticated routes.

33. **What is authorization, and how is it different from authentication?**<br>
    Authentication identifies the signed-in user. Authorization decides
    whether that user may perform an action; CareConnect uses role and
    ownership checks for this.

34. **How does CSRF protection work here?**<br>
    A session-bound token is included in forms. Before state-changing HTTP
    methods run, the application compares submitted and expected tokens with
    `secrets.compare_digest`.

35. **Why reject external login redirect targets?**<br>
    It prevents the login flow from redirecting users to an arbitrary external
    destination supplied in the URL.

36. **What security response headers are set?**<br>
    The app sets `X-Content-Type-Options`, `X-Frame-Options`,
    `Referrer-Policy`, and `Permissions-Policy`; non-static responses are
    marked `private, no-store`.

37. **What does `SESSION_COOKIE_HTTPONLY` do?**<br>
    It prevents ordinary client-side JavaScript from reading the session
    cookie. It does not replace other security controls.

38. **What is the Secure cookie setting for?**<br>
    When enabled with `SESSION_COOKIE_SECURE=1`, browsers should send the
    cookie only over HTTPS.

39. **What does the audit log contain?**<br>
    Action, entity type and ID, timestamp, actor information when available,
    and a safe summary intended to avoid passwords, clinical details, and
    payment credentials.

40. **Is the audit log tamper-proof?**<br>
    No. SQLite triggers block ordinary updates/deletions, but privileged
    access to the database file can bypass application-level protections. It
    is not an external tamper-resistant logging system.

41. **How many audit events are displayed?**<br>
    The administrator page retrieves at most the latest 200, ordered by event
    time and then ID in descending order.

42. **What happens when an unexpected server error occurs?**<br>
    The error handler rolls back the database session, logs the exception,
    and returns a generic error page rather than exposing internal details.

### Testing, limitations, and future work

43. **How are tests isolated from the real database?**<br>
    The test case configures an in-memory SQLite database, creates its
    schema, and tears it down after each test. A separate persistence test
    uses a temporary file-backed database.

44. **What is the recorded automated test result?**<br>
    The completion report records 56 tests in 47.326 seconds with result
    `OK`.

45. **What areas do the tests cover?**<br>
    Authentication, registration, roles, doctors, appointments, records,
    invoices, audit behavior, dashboard statistics, migrations, headers, and
    database integrity.

46. **What is the purpose of a foreign key?**<br>
    It links related rows and helps prevent references to nonexistent parent
    rows, subject to database enforcement and connection configuration.

47. **What does `db.create_all()` do, and what does it not do?**<br>
    It creates missing tables for defined models. It is not a general
    version-controlled migration system and does not by itself evolve every
    existing schema.

48. **What is a key limitation of the current application?**<br>
    It is an educational/local demonstration, uses the Flask development
    server in the documented local run, has a simulated payment flow, and is
    not a compliance-certified production clinical system.

49. **What would you improve next?**<br>
    A production deployment stack, versioned migrations, scheduled overdue
    processing, external protected audit storage, and formal security/privacy
    review are reasonable next steps. A real payment integration would
    require a secure provider and separate scope.

50. **What is the most important conclusion about the project?**<br>
    CareConnect demonstrates connected role-based workflows and automated
    verification, while clearly retaining important limits around production
    operations, payments, audit tamper resistance, and compliance.
