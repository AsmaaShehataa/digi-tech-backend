# Standalone Backend Repository Guide

Use this guide when you host the website frontend on static hosting (for example Hostinger shared hosting) and host the backend on a Python-friendly service.

## Recommended repository names

- Frontend repo: `digi-tech-frontend`
- Backend repo: `digi-tech-backend`

## Files to keep in the backend repo

- `admin_backend.py`
- `requirements.txt`
- `templates/admin_dashboard.html`
- `templates/admin_login.html`
- `static/admin.css`
- `static/admin-auth.css`
- `static/admin.js`
- `deploy/hostinger/*`

## Backend environment variables

Required:

- `FLASK_SECRET_KEY`
- `APP_DEPLOY_TARGET` (`public` or `admin_internal`)

Admin/Internal mode:

- `ADMIN_EMAIL`
- `ADMIN_PASSWORD` or `ADMIN_PASSWORD_HASH`
- `ADMIN_ALLOWED_IPS` (optional but recommended)

Cross-origin (frontend on separate domain):

- `PUBLIC_API_ALLOWED_ORIGINS`
  - Example:
    - `PUBLIC_API_ALLOWED_ORIGINS=https://www.digi-tech.com,https://digi-tech.com`
- `PUBLIC_API_ALLOW_CREDENTIALS=1` (optional)

## Public + admin runtime split

Run two backend processes:

1. Public process (`APP_DEPLOY_TARGET=public`)
2. Internal admin process (`APP_DEPLOY_TARGET=admin_internal`)

For Hostinger VPS, use the provided examples under `deploy/hostinger/` (`nginx` + `systemd`).

## Quick smoke test

```bash
python3 -m pip install -r requirements.txt
APP_DEPLOY_TARGET=public APP_PORT=5000 python3 admin_backend.py
```

Then test:

- `GET /api/public/health` -> `200`
- `POST /api/public/inquiries` -> `201`

Admin routes should be unavailable in public mode:

- `GET /admin` -> `404`

