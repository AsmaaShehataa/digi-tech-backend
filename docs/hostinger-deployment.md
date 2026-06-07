# Hostinger Deployment: Public + Internal Admin Split

This project supports two deployment targets using the same backend codebase:

- `APP_DEPLOY_TARGET=public` → serves public website + public API only
- `APP_DEPLOY_TARGET=admin_internal` → enables admin login/dashboard + admin APIs

Admin module is **disabled** in public mode and returns `404`.

## Why this split

- Public users should only access frontend and public-safe APIs.
- Admin dashboard and admin APIs should stay internal for staff only.
- Internal admin host can be protected by:
  - IP allowlist (`ADMIN_ALLOWED_IPS`)
  - login session auth (email/password)

## Environment configuration

### Public service env

- `APP_DEPLOY_TARGET=public`
- `FLASK_SECRET_KEY=<strong-secret>`
- `DATABASE_URL=<postgres-connection-string>`

Optional:

- `ADMIN_EMAIL` / `ADMIN_PASSWORD` are ignored in public mode.

### Internal admin service env

- `APP_DEPLOY_TARGET=admin_internal`
- `FLASK_SECRET_KEY=<strong-secret>`
- `DATABASE_URL=<postgres-connection-string>`
- `ADMIN_EMAIL=<admin email>`
- `ADMIN_PASSWORD=<admin password>` or `ADMIN_PASSWORD_HASH=<hash>`
- `ADMIN_ALLOWED_IPS=<comma-separated CIDRs>`
  - Example: `203.0.113.44/32,198.51.100.0/24`

### Database backend behavior

- If `DATABASE_URL` is set, app uses PostgreSQL (recommended for Render and production).
- If `DATABASE_URL` is not set, app falls back to local SQLite (`data/admin_dashboard.db`).
- Render free web service filesystem is ephemeral, so SQLite should be used for local/dev only.

## Public API routes

- `GET /api/public/health`
- `POST /api/public/inquiries`

## Admin routes (enabled only in `admin_internal`)

- `GET/POST /admin/login`
- `POST /admin/logout`
- `GET /admin`
- `POST /api/admin/login`
- `POST /api/admin/logout`
- `GET/POST/PUT/DELETE /api/admin/projects...`
- `GET /api/admin/overview`
- `GET /api/admin/export.csv`
- `POST /api/admin/share-report`

## Nginx setup

Use two server blocks:

- Public domain (`example.com`) → upstream `127.0.0.1:5000` (public app)
- Internal admin subdomain (`admin.example.com`) → upstream `127.0.0.1:5001` (admin app)
  - plus Nginx `allow/deny` IP rules

Reference configs are provided in:

- `deploy/hostinger/nginx-public.conf`
- `deploy/hostinger/nginx-admin-internal.conf`

## systemd setup

Reference service files:

- `deploy/hostinger/systemd/digi-tech-public.service`
- `deploy/hostinger/systemd/digi-tech-admin.service`

These run the same `admin_backend.py` app with different environment files and ports.
