# CareConnect on AWS EC2 (Ubuntu)

This guide deploys the existing Flask application behind Gunicorn and Nginx.
It does not create AWS resources and does not make the application a
production-certified clinical system. The application stores medical and
account information; deploy only with data you are authorized to host and
protect.

## What was inspected

- The application factory and initialization are in `app.py`. It registers six
  route blueprints, applies CSRF and response-header hooks, installs error
  handlers, and runs `create_all()` plus targeted compatibility migrations.
- The blueprints cover authentication, dashboards/directories, administration,
  appointments, medical records, and billing. Role checks and
  patient/doctor ownership checks are implemented in the routes.
- `models/__init__.py` defines nine ORM tables, including medical-record
  attachment metadata. Uploaded bytes are stored separately from SQLite.
- Jinja pages are under `templates/`; responsive CSS and navigation behavior
  are in `static/css/style.css` and `static/js/app.js`.
- The existing application is configured for SQLite by default, using
  `C:\Users\Rakes\Hospital-Management-System\instance\careconnect.db` on this
  Windows checkout when `DATABASE_URL` is unset. `UPLOAD_FOLDER` defaults to
  `C:\Users\Rakes\Hospital-Management-System\instance\uploads`; that directory
  is currently absent. This deployment points `DATABASE_URL` to
  `/var/lib/careconnect/careconnect.db`, outside versioned application code.
- The original project already had pinned Flask/ORM dependencies and
  `python-dotenv`; Gunicorn was missing. The only environment file found was
  `.env.example`; no local `.env`/`.env.local` or credential store was found.
  The filename scan also found a public CA bundle and a Playwright source module
  under `venv`, not private key material. No `.git` metadata exists in this
  workspace, so tracked status cannot be verified here.
- No `/healthz` route existed before deployment preparation.

## Changes made for deployment

- Load `.env` values when the app is imported by a process manager such as
  Gunicorn; require a configured secret and reject debug mode when
  `CARECONNECT_ENV=production`.
- Set Flask `DEBUG` off by default and add `/healthz`, which returns 200 only
  when the application can query its configured database.
- Add the Gunicorn dependency and a loopback-only Gunicorn configuration.
- Add a Nginx reverse-proxy template, a secret-free `.env.example`, and this
  installation guide.
- Add private medical-record attachments (PDF/PNG/JPEG, up to 10 MB total per
  save), with randomized storage names, metadata in SQLite, and ownership-aware
  downloads. The local default is `instance/uploads`; EC2 should use the
  persistent `/var/lib/careconnect/uploads` path from `.env.example`.
- Align Nginx's request-body limit with the Flask attachment limit.

No AWS deployment is performed by these changes. The existing
`instance/careconnect.db` file is not modified by the guide or source changes.

## 1. Prepare AWS resources

In the AWS Console, create an EC2 instance using an Ubuntu Server 24.04 LTS
AMI in your chosen Region. Select an instance size suitable for a small demo
and your expected traffic. Use an encrypted EBS root volume; do not store the
database on instance-store storage. Require IMDSv2 in the instance metadata
options.

Configure the security group with:

- TCP 22 only from your current trusted IP, or use Systems Manager Session
  Manager instead of public SSH.
- TCP 80 and 443 for web traffic. Open 443 before enabling public HTTPS.
- **Do not** expose Gunicorn port 8000.

Point a DNS name (for example `careconnect.example.com`) to the instance's
stable public address before requesting a TLS certificate. Prefer an Elastic
IP or another stable address if the DNS name must survive stop/start.

Do not attach broad AWS credentials or put secrets in EC2 user data. The app
does not need AWS API credentials. If using Session Manager, attach only its
required managed-instance permissions.

## 2. Connect and install Ubuntu packages

From Windows PowerShell, connect using the Ubuntu AMI username:

```powershell
ssh -i .\your-key.pem ubuntu@YOUR_EC2_PUBLIC_DNS
```

On the instance:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip build-essential \
  libsqlite3-dev sqlite3 nginx certbot python3-certbot-nginx
```

Create a restricted service account and persistent data directory:

```bash
sudo adduser --system --group --home /opt/careconnect careconnect
sudo install -d -o root -g careconnect -m 0750 /opt/careconnect/releases
sudo install -d -o careconnect -g careconnect -m 0750 /var/lib/careconnect
sudo install -d -o careconnect -g careconnect -m 0750 /var/lib/careconnect/uploads
sudo install -d -o root -g careconnect -m 0750 /etc/careconnect
```

## 3. Back up the live database and uploaded documents

The current local database is
`C:\Users\Rakes\Hospital-Management-System\instance\careconnect.db`.
The configured local upload directory is
`C:\Users\Rakes\Hospital-Management-System\instance\uploads`; it currently
does not exist. Reconfirm both settings on the source machine before migration:
the application can override them with `DATABASE_URL` and `UPLOAD_FOLDER`.
Inspect configuration without printing `SECRET_KEY` or other credentials.

Do not use `Copy-Item` on a live SQLite database. Use SQLite's online Backup API
to take a consistent snapshot while Flask remains running. Save backups outside
the repository on an encrypted, access-restricted drive (for example, a
BitLocker-protected volume); do not place them in a shared/synchronized folder.
The following PowerShell sequence writes only the backup destination; it opens
the source database read-only:

```powershell
Set-Location C:\Users\Rakes\Hospital-Management-System
$sourceDb = (Resolve-Path .\instance\careconnect.db).Path
$backupDir = Join-Path $env:USERPROFILE "CareConnect-Private-Backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$stamp = (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N")
$dbBackup = Join-Path $backupDir "careconnect-$stamp.sqlite"
@'
import sqlite3
import sys
from pathlib import Path

source_path, backup_path = sys.argv[1:3]
backup_path = Path(backup_path)
partial_path = Path(str(backup_path) + ".partial")
partial_path.touch(exist_ok=False)
source = None
try:
    source = sqlite3.connect(Path(source_path).as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(partial_path)
    try:
        destination.execute("PRAGMA journal_mode=DELETE")
        source.backup(destination, pages=128, sleep=0.25)
        destination.commit()
    finally:
        destination.close()
    check_db = sqlite3.connect(partial_path.as_uri() + "?mode=ro", uri=True)
    try:
        result = check_db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
    finally:
        check_db.close()
    partial_path.rename(backup_path)
except Exception:
    if partial_path.exists():
        partial_path.unlink()
    raise
finally:
    if source is not None:
        source.close()
print(f"Verified SQLite backup: {backup_path}")
'@ | & .\venv\Scripts\python.exe - $sourceDb $dbBackup
if ($LASTEXITCODE -ne 0) { throw "SQLite backup failed; do not transfer a partial backup." }
Get-FileHash $dbBackup -Algorithm SHA256
```

The backup may contain personal and health information. Restrict access to the
backup directory and transfer it only over an approved encrypted channel. Keep
the hash with the backup inventory, not with public source code.

Back up uploaded documents as a separate archive. Do not mix uploads with the
source release or database archive. Avoid running this while uploads are being
written; schedule a brief upload pause or use a consistent filesystem snapshot
so the directory snapshot agrees with the database metadata.

```powershell
$uploadDir = (Resolve-Path .\instance\uploads -ErrorAction SilentlyContinue)
$uploadBackup = $null
if ($uploadDir) {
  $uploadBackup = Join-Path $backupDir "careconnect-uploads-$stamp.tar.gz"
  $uploadPartial = "$uploadBackup.partial"
  tar -czf $uploadPartial -C . instance/uploads
  if ($LASTEXITCODE -ne 0) { throw "Upload archive creation failed." }
  tar -tzf $uploadPartial > $null
  if ($LASTEXITCODE -ne 0) { throw "Upload archive verification failed." }
  Move-Item -LiteralPath $uploadPartial -Destination $uploadBackup
  Get-FileHash $uploadBackup -Algorithm SHA256
} else {
  Write-Output "No local uploads directory exists; no upload archive created."
}
```

The current inventory found no upload directory, so there are no local
attachments to migrate from this checkout. Do not create an empty archive just
to imply there are files to restore.

## 4. Package and transfer the existing application

Build a sanitized staging tree outside the project, excluding environment
secrets, databases, uploads, credentials, and virtual environments. Add back
only the reviewed, secret-free `.env.example`. The archive and staging tree
are outside the project so the archive cannot accidentally include itself:

```powershell
$sourceStage = Join-Path $env:TEMP ("careconnect-source-" + [guid]::NewGuid().ToString("N"))
$appArchive = Join-Path $backupDir "careconnect-app-reviewed-$stamp.tar.gz"
try {
  New-Item -ItemType Directory -Path $sourceStage | Out-Null
  robocopy . $sourceStage /E `
    /XD venv .venv __pycache__ .git uploads instance\uploads .aws .ssh secrets `
    /XF .env .env.* *.env .envrc .flaskenv *.db *.sqlite *.sqlite3 *.db-* `
        *.sqlite-* *.sqlite3-* *.bak *.backup *.dump *.pem *.key *.p12 *.pfx `
        *.secret *.token *.password *.credentials *uploads*.tar *uploads*.tar.gz `
        *uploads*.zip id_rsa id_ed25519 credentials credentials.* secrets.* `
        passwords.*
  if ($LASTEXITCODE -ge 8) { throw "Sanitized source copy failed (robocopy $LASTEXITCODE)." }
  Copy-Item .\.env.example (Join-Path $sourceStage '.env.example')
  tar -czf $appArchive -C $sourceStage .
  if ($LASTEXITCODE -ne 0) { throw "Source archive creation failed." }
} finally {
  if (Test-Path -LiteralPath $sourceStage) {
    Remove-Item -LiteralPath $sourceStage -Recurse -Force
  }
}
Get-FileHash $appArchive -Algorithm SHA256
```

Transfer the reviewed application archive, the SQLite backup, and the optional
uploads archive as distinct files. Never transfer local `.env`/credential
files. Only migrate actual patient data with explicit authorization and
appropriate privacy/security approval.

```powershell
scp $appArchive ubuntu@YOUR_EC2_PUBLIC_DNS:/tmp/careconnect-app-reviewed.tar.gz
scp $dbBackup ubuntu@YOUR_EC2_PUBLIC_DNS:/tmp/careconnect-initial.sqlite
if (Test-Path $uploadBackup) {
  scp $uploadBackup ubuntu@YOUR_EC2_PUBLIC_DNS:/tmp/careconnect-uploads.tar.gz
}
```

On the instance, compare the transferred database and application archive
checksums with the source `Get-FileHash` values before extraction or
installation. If the optional upload archive exists, verify it too:

```bash
sha256sum /tmp/careconnect-initial.sqlite /tmp/careconnect-app-reviewed.tar.gz
# Optional, only when an upload archive was transferred:
sha256sum /tmp/careconnect-uploads.tar.gz
```

On Ubuntu, unpack into a timestamped release directory:

```bash
RELEASE="$(date -u +%Y%m%d%H%M%S)"
sudo install -d -o root -g careconnect -m 0750 "/opt/careconnect/releases/$RELEASE"
sudo tar -xzf /tmp/careconnect-app-reviewed.tar.gz -C "/opt/careconnect/releases/$RELEASE"
sudo chown -R root:careconnect "/opt/careconnect/releases/$RELEASE"
sudo find "/opt/careconnect/releases/$RELEASE" -type d -exec chmod 0750 {} \;
sudo find "/opt/careconnect/releases/$RELEASE" -type f -exec chmod 0640 {} \;
sudo ln -sfn "/opt/careconnect/releases/$RELEASE" /opt/careconnect/current
sudo install -d -o root -g careconnect -m 0750 /opt/careconnect/current/instance
```

When an upload archive was transferred, merge it into the persistent directory
without overwriting any files already on the instance:

```bash
if [ -f /tmp/careconnect-uploads.tar.gz ]; then
  sudo tar -xzf /tmp/careconnect-uploads.tar.gz -C /tmp
  sudo cp -an /tmp/instance/uploads/. /var/lib/careconnect/uploads/
  sudo chown -R careconnect:careconnect /var/lib/careconnect/uploads
  sudo find /var/lib/careconnect/uploads -type d -exec chmod 0750 {} \;
  sudo find /var/lib/careconnect/uploads -type f -exec chmod 0640 {} \;
fi
```

Build the Python environment from the project manifest:

```bash
sudo python3 -m venv /opt/careconnect/venv
sudo /opt/careconnect/venv/bin/python -m pip install --upgrade pip
sudo /opt/careconnect/venv/bin/pip install \
  -r /opt/careconnect/current/requirements.txt
```

## 5. Install the existing SQLite database and uploads safely

The application automatically creates missing tables and applies its targeted
compatibility migrations at startup. Preserve the verified SQLite backup as
the migration source. Check its hash against the source inventory before
installing; do not make a raw copy of the active database.

If `/var/lib/careconnect/careconnect.db` already exists, **do not overwrite
it**. Stop and compare backups and migration plans before proceeding.

For a first deployment where the target database does not exist:

```bash
if [ -e /var/lib/careconnect/careconnect.db ]; then
  echo "Target database already exists; refusing to overwrite it."
  exit 1
fi
sudo install -o careconnect -g careconnect -m 0640 \
  /tmp/careconnect-initial.sqlite /var/lib/careconnect/careconnect.db
sudo sqlite3 /var/lib/careconnect/careconnect.db 'PRAGMA integrity_check;'
```

The integrity check must print `ok`. The directory is owned by the service
account because SQLite may need to create journal files alongside the
database. For backups on Ubuntu, use SQLite's Backup API as well; do not
copy the live `.db` file with `cp`:

```bash
sudo install -d -o careconnect -g careconnect -m 0700 /var/backups/careconnect
sudo -u careconnect /opt/careconnect/venv/bin/python - <<'PY'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

source_path = Path("/var/lib/careconnect/careconnect.db")
target_path = Path("/var/backups/careconnect") / (
    "careconnect-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    + "-" + uuid4().hex + ".sqlite"
)
partial_path = Path(str(target_path) + ".partial")
if target_path.exists():
    raise FileExistsError(target_path)
partial_path.touch(exist_ok=False)
source = None
try:
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(partial_path)
    try:
        destination.execute("PRAGMA journal_mode=DELETE")
        source.backup(destination, pages=128, sleep=0.25)
        destination.commit()
    finally:
        destination.close()
    check_db = sqlite3.connect(partial_path.as_uri() + "?mode=ro", uri=True)
    try:
        result = check_db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
    finally:
        check_db.close()
    partial_path.rename(target_path)
except Exception:
    if partial_path.exists():
        partial_path.unlink()
    raise
finally:
    if source is not None:
        source.close()
print(target_path)
PY
sudo chmod 0600 /var/backups/careconnect/*.sqlite
```

Back up the upload directory independently, during a write-free window:

```bash
sudo tar -czf "/var/backups/careconnect/uploads-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" \
  -C /var/lib/careconnect uploads
sudo chmod 0600 /var/backups/careconnect/uploads-*.tar.gz
```

If the database contains real patient information, backups and transfers are
sensitive health data. Encrypt backups at rest and in transit, restrict access,
verify hashes after transfer, and apply an approved retention policy.

### Restore on Ubuntu EC2

Restore only during a planned maintenance window, after stopping the service.
Never replace an existing database or upload directory without first making a
separate recoverable copy. The following example restores into a previously
empty target; if either target already exists, stop and investigate rather
than overwrite it.

```bash
sudo systemctl stop careconnect
test ! -e /var/lib/careconnect/careconnect.db.restore || {
  echo "Restore staging database already exists; inspect it before continuing."
  exit 1
}
test ! -e /var/lib/careconnect/careconnect.db || {
  echo "Database exists; preserve and review it before restore."
  exit 1
}
sudo install -o careconnect -g careconnect -m 0640 \
  /tmp/careconnect-initial.sqlite \
  /var/lib/careconnect/careconnect.db.restore
CHECK="$(sudo sqlite3 /var/lib/careconnect/careconnect.db.restore 'PRAGMA integrity_check;')"
if [ "$CHECK" != "ok" ]; then
  echo "Restored database failed integrity_check; preserve the restore file and investigate."
  exit 1
fi
FOREIGN_KEYS="$(sudo sqlite3 /var/lib/careconnect/careconnect.db.restore 'PRAGMA foreign_key_check;')"
if [ -n "$FOREIGN_KEYS" ]; then
  echo "Restored database has foreign-key violations; preserve it and investigate."
  exit 1
fi
sudo mv /var/lib/careconnect/careconnect.db.restore \
  /var/lib/careconnect/careconnect.db
```

For uploads, extract into a new private staging directory, inspect it, and
refuse to replace a non-empty destination:

```bash
RESTORE_STAGE="/var/lib/careconnect/restore-staging-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$RESTORE_STAGE" || exit 1
sudo install -d -o careconnect -g careconnect -m 0750 "$RESTORE_STAGE"
sudo tar -tzf /tmp/careconnect-uploads.tar.gz
sudo tar -xzf /tmp/careconnect-uploads.tar.gz -C "$RESTORE_STAGE"
if [ -e /var/lib/careconnect/uploads ]; then
  if [ -n "$(sudo find /var/lib/careconnect/uploads -mindepth 1 -print -quit)" ]; then
    echo "Uploads directory is non-empty; preserve and review it before restore."
    exit 1
  fi
  test ! -e /var/lib/careconnect/uploads.empty-before-restore || exit 1
  sudo mv /var/lib/careconnect/uploads /var/lib/careconnect/uploads.empty-before-restore
fi
sudo mv "$RESTORE_STAGE/instance/uploads" \
  /var/lib/careconnect/uploads
sudo chown -R careconnect:careconnect /var/lib/careconnect/uploads
sudo find /var/lib/careconnect/uploads -type d -exec chmod 0750 {} \;
sudo find /var/lib/careconnect/uploads -type f -exec chmod 0640 {} \;
sudo rmdir "$RESTORE_STAGE/instance" "$RESTORE_STAGE"
```

Before starting the app, verify `PRAGMA integrity_check`, `PRAGMA
foreign_key_check`, and that each attachment metadata row refers to a file in
the restored uploads directory. Start the service only after the database and
files have both been validated. Keep the original backup unchanged until the
application is verified:

```bash
sudo systemctl start careconnect
sudo systemctl status careconnect --no-pager
sudo journalctl -u careconnect -n 100 --no-pager
```

## 6. Configure environment variables

Create the service environment file with restricted permissions:

```bash
sudo install -o root -g careconnect -m 0640 \
  /opt/careconnect/current/.env.example /etc/careconnect/careconnect.env
sudoedit /etc/careconnect/careconnect.env
```

Set a unique secret on the EC2 host (do not reuse a repository example):

```bash
openssl rand -hex 32
```

Put its output into `SECRET_KEY` using `sudoedit`; do not paste it into source
control, chat, shell history, or this guide. Confirm the file has these
settings:

```dotenv
CARECONNECT_ENV=production
SECRET_KEY=<your-private-random-value>
DATABASE_URL=sqlite:////var/lib/careconnect/careconnect.db
UPLOAD_FOLDER=/var/lib/careconnect/uploads
FLASK_DEBUG=0
SESSION_COOKIE_SECURE=1
```

`SESSION_COOKIE_SECURE=1` is appropriate when serving the application over
HTTPS. The provided `.env.example` uses this secure value. Do not rely on
HTTP for signed-in use.

## 7. Run Gunicorn with systemd

Create `/etc/systemd/system/careconnect.service`:

```ini
[Unit]
Description=CareConnect Flask application
After=network.target

[Service]
Type=simple
User=careconnect
Group=careconnect
WorkingDirectory=/opt/careconnect/current
EnvironmentFile=/etc/careconnect/careconnect.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/careconnect/venv/bin/gunicorn --config deployment/gunicorn_config.py app:app
Restart=on-failure
RestartSec=5
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/careconnect /var/lib/careconnect/uploads

[Install]
WantedBy=multi-user.target
```

Write the file with:

```bash
sudoedit /etc/systemd/system/careconnect.service
sudo systemctl daemon-reload
sudo systemctl enable --now careconnect
sudo systemctl status careconnect --no-pager
sudo journalctl -u careconnect -n 100 --no-pager
```

The provided Gunicorn config binds only to `127.0.0.1:8000`, uses two
synchronous workers, preloads the app so startup migrations run once before
workers fork, and disposes inherited SQLAlchemy pools in each worker. The
Nginx proxy is the only network-facing route to Gunicorn. The service user
needs write access to the database directory and upload directory. Keep both
on persistent EBS-backed storage, not instance store, and include both in
backups. The app does not include antivirus scanning; inspect/quarantine
uploads before using this feature with real clinical documents.

## 8. Configure Nginx and TLS

Install the provided Nginx site and replace `example.com` with the DNS name:

```bash
sudo install -m 0644 /opt/careconnect/current/deployment/nginx.conf \
  /etc/nginx/sites-available/careconnect
sudoedit /etc/nginx/sites-available/careconnect
sudo ln -sfn /etc/nginx/sites-available/careconnect \
  /etc/nginx/sites-enabled/careconnect
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

Once the DNS record points to this instance and inbound TCP 80/443 is
permitted, obtain and configure a certificate:

```bash
sudo certbot --nginx -d careconnect.example.com
sudo nginx -t
sudo systemctl reload nginx
```

Follow Certbot's prompt to redirect HTTP to HTTPS. Verify the site at
`https://careconnect.example.com`. Do not send credentials or patient data
over plain HTTP. Keep the Nginx configuration and certificate renewal checked
after changes.

## 9. Verify deployment and application behavior

On the instance:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/healthz
curl --fail --silent --show-error https://careconnect.example.com/healthz
sudo systemctl is-active careconnect
sudo systemctl is-active nginx
sudo sqlite3 /var/lib/careconnect/careconnect.db 'PRAGMA integrity_check;'
sudo sqlite3 /var/lib/careconnect/careconnect.db 'PRAGMA foreign_key_check;'
```

The health endpoint returns `{"status":"ok"}` when it can query the configured
database; it returns HTTP 503 with a generic status when that check fails.
Then use synthetic test accounts to verify registration/login, role-specific
access, doctor and patient directories, appointment booking/status changes,
medical-record ownership, invoices, and the admin-only audit page. The payment
flow remains a simulation; no real payment is performed. Do not test by
changing or deleting production records.

For local development or pre-deployment testing, the repository test command
is `python -m unittest discover -s tests -v`. The test suite creates isolated
in-memory databases, but importing `app` also creates a module-level app.
Point that app to a temporary file as well:

```powershell
$testDb = Join-Path $env:TEMP ("careconnect-tests-" + [guid]::NewGuid().ToString("N") + ".sqlite")
$env:DATABASE_URL = "sqlite:///" + $testDb.Replace("\", "/")
$env:SECRET_KEY = "isolated-test-only-secret"
python -m unittest discover -s tests -v
Remove-Item Env:\DATABASE_URL
Remove-Item Env:\SECRET_KEY
```

The deployment guide's commands do not execute the tests automatically and
are not evidence that an AWS deployment succeeded.

## 10. Backups and upgrades

Before deploying an application update:

1. Stop the service and create a SQLite backup (or an EBS snapshot plus a
   consistent SQLite backup); back up `/var/lib/careconnect/uploads` as well.
2. Deploy source into a new release directory; never extract over the active
   release.
3. Keep the same `/var/lib/careconnect/careconnect.db` and persistent
   environment file, and do not replace the uploads directory during release
   deployment.
4. Review schema changes, start the new release, and check logs, `/healthz`,
   and `PRAGMA integrity_check`.
5. Keep the previous release and backup until the updated app is verified.

This application does not currently use a versioned database migration
framework. For production clinical use, obtain dedicated security, privacy,
backup/recovery, compliance, and operational review. The development server
and demo payment simulation are not production substitutes.
