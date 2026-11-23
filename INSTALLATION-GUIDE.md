# CareConnect Installation Guide

## Windows local installation

### 1. Prerequisites

- Windows 10/11
- Python 3.11 or newer; the current environment is Python 3.13
- PowerShell

### 2. Install

```powershell
Set-Location C:\Users\Rakes\Hospital-Management-System
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Configure

```powershell
$env:SECRET_KEY = (& python -c "import secrets; print(secrets.token_hex(32))")
$env:FLASK_DEBUG = "0"
```

The application also supports `DATABASE_URL`, `UPLOAD_FOLDER`, and `SESSION_COOKIE_SECURE`. Do not add real secrets to `.env.example`.

### 4. Initialize

```powershell
flask --app app init-db
flask --app app create-admin
```

The existing `instance/careconnect.db` is preserved. Do not delete or recreate it when it contains real or demonstration data you need.

### 5. Run

```powershell
flask --app app run --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000`.

## Optional development seed

```powershell
$env:CARECONNECT_ALLOW_DEMO_SEED = "1"
flask --app app seed-doctors --count 3
Remove-Item Env:\CARECONNECT_ALLOW_DEMO_SEED
```

Use seed doctors only in a disposable development database.

## Ubuntu server installation

See [AWS-DEPLOYMENT-GUIDE.md](AWS-DEPLOYMENT-GUIDE.md) for the verified Ubuntu, Gunicorn, Nginx, systemd, HTTPS, backup, and environment configuration steps.
