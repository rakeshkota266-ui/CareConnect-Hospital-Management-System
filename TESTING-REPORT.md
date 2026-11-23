# CareConnect Testing Report

## Test date

Verified on 23 November 2026.

## Test command

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Result

- Tests: 68
- Failures: 0
- Errors: 0
- Result: `OK`

The suite emits resource warnings from temporary SQLite/upload test objects when run in this Windows environment. These warnings did not fail tests or indicate an application response failure.

## Coverage

The suite covers:

- application startup and health checks;
- registration, login, logout, password hashing, and external redirect rejection;
- role authorization and patient/doctor/admin ownership boundaries;
- doctor and patient administration;
- appointment booking, duplicate detection, and status transitions;
- medical-record creation, editing, ownership, and attachment validation/download;
- invoice creation, amounts, due dates, overdue transitions, status changes, and printing;
- demo-payment simulation and idempotency;
- audit event creation and persistence;
- database migrations and integrity;
- CSRF, security headers, and caching behavior;
- SQLite foreign-key and uniqueness behavior.

## Test isolation

Tests use isolated in-memory or temporary SQLite databases and synthetic names, diagnoses, and accounts. Do not point test runs at `instance/careconnect.db` or another database containing real patient information.
