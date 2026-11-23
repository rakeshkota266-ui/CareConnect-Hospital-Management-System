# CareConnect Security Report

## Scope

This report describes application-level safeguards verified from source and automated tests. It is not a penetration test or a healthcare compliance certification.

## Verified controls

- Werkzeug password hashing is used for account passwords.
- Flask-Login protects authenticated routes.
- Route decorators enforce role membership.
- Patient routes enforce patient ownership.
- Doctor routes enforce doctor assignment/authorship.
- State-changing form requests require a session-bound CSRF token.
- Login rejects external redirect targets.
- Session and remember cookies are HTTP-only and `SameSite=Lax`.
- Secure cookies can be enabled with `SESSION_COOKIE_SECURE=1`.
- Dynamic responses use `Cache-Control: private, no-store`.
- Responses include `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, and CSP headers.
- HSTS is emitted for secure HTTPS requests.
- Production startup rejects missing `SECRET_KEY` and enabled Flask debug mode.
- Medical-record uploads allow only PDF, PNG, and JPEG signatures and limit each file and total request size.
- Stored attachment names are randomized and do not use the original filename.
- Database and form validation reject malformed values and invalid transitions.
- SQLite foreign-key enforcement is enabled for SQLite connections.
- Audit summaries omit clinical details and payment credentials.

## Payment safety

The demo-payment module has no card fields and makes no external payment-provider call. It changes an invoice to `paid` and stores a synthetic reference only.

## Key limitations

- A persistent HTTPS deployment requires a real TLS certificate and a persistent secret key.
- The SQLite audit log is not an external tamper-resistant security log.
- Upload signature checks are not malware scanning or document-content validation.
- Password complexity is currently minimum-length based; stronger policy and account recovery may be needed for real use.
- The application does not provide encrypted database-at-rest, MFA, rate limiting, external centralized logging, DLP, or audit access monitoring.
- Real clinical use requires professional privacy, security, retention, backup, incident-response, and compliance review.
