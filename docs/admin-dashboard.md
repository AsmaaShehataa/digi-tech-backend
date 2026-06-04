# Admin Dashboard Architecture

This document describes the new centralized admin system for project and payment operations.

## High-level flow

1. **Admin opens `/admin`**
2. If unauthenticated, system redirects to `/admin/login`
3. Admin authenticates with email/password
4. Dashboard loads overview + projects from API:
   - `GET /api/admin/overview?currency=USD|EGP`
   - `GET /api/admin/projects?currency=USD|EGP`
5. Admin creates projects through the input form:
   - `POST /api/admin/projects`
6. System automatically calculates:
   - remaining balance
   - payment progress
   - overdue/pending milestones
   - deadline urgency
   - portfolio financial summaries
7. Admin exports or shares reports:
   - `GET /api/admin/export.csv`
   - `GET /api/admin/export.json`
   - `POST /api/admin/share-report`

## Backend modules

- `admin_backend.py`
  - Flask route handlers
  - SQLite repository and schema initialization
  - session-based authentication middleware
  - financial/operational metrics engine
  - reporting/export logic
- `templates/admin_login.html`
  - secure admin login UI
- `templates/admin_dashboard.html`
  - dashboard UI layout and workflow
- `static/admin.js`
  - UI state management, API integration, render logic
- `static/admin.css`
  - modern dashboard styling

## Database schema (SQLite)

### `projects`

- `id` (PK)
- `client_name` (TEXT, required)
- `project_name` (TEXT, required)
- `currency` (TEXT, required: USD or EGP)
- `total_price` (REAL, required)
- `paid_amount` (REAL, required)
- `start_date` (TEXT ISO date)
- `deadline` (TEXT ISO date)
- `status` (TEXT: planned, in_progress, on_hold, completed, cancelled)
- `notes` (TEXT optional)
- `created_at`, `updated_at`

### `payment_milestones`

- `id` (PK)
- `project_id` (FK → projects.id)
- `title` (TEXT, required)
- `due_date` (TEXT ISO date)
- `amount` (REAL, required)
- `paid` (INTEGER boolean)
- `paid_date` (TEXT optional)
- `created_at`

### `admin_users`

- `id` (PK)
- `email` (TEXT, unique)
- `password_hash` (TEXT, werkzeug hash)
- `is_active` (INTEGER boolean)
- `created_at`
- `last_login_at`

## Route map

### UI routes

- `GET /` → public website
- `GET /admin/login` → admin sign-in
- `GET /admin` → admin dashboard
- `POST /admin/logout` → end admin session

### API routes

- `POST /api/admin/login`
- `POST /api/admin/logout`
- `GET /api/admin/projects?currency=USD|EGP`
- `POST /api/admin/projects`
- `GET /api/admin/projects/<project_id>`
- `PATCH /api/admin/projects/<project_id>`
- `GET /api/admin/overview?currency=USD|EGP`
- `GET /api/admin/export.csv?currency=USD|EGP` (optional filter)
- `GET /api/admin/export.json?currency=USD|EGP` (optional filter)
- `POST /api/admin/share-report`

## Automated logic

For each project, API responses include computed metrics:

- `remaining_balance`
- `payment_progress`
- `days_remaining`
- `deadline_state` (on_track, upcoming, overdue)
- `pending_milestones_count`
- `overdue_milestones_count`
- `next_due_milestone`
- `effective_status` (auto-completes when fully paid unless cancelled)

Portfolio-level summary includes:

- total/active/completed projects
- pending & overdue payment counts/amounts
- upcoming deadlines (next 14 days)
- total contract value, paid, remaining
- portfolio payment progress percentage

## Currency behavior

- Admin selects a project currency during creation (`USD` / `EGP`).
- Dashboard overview and project table can be filtered by selected currency.
- Exports can include all currencies or be filtered by a specific one.
- No currency conversion is applied automatically; values are tracked natively per project currency.

## Authentication behavior

- `/admin` and all operational admin APIs require a valid authenticated session.
- Unauthenticated web requests are redirected to `/admin/login`.
- Unauthenticated API requests return `401`.
- Default bootstrap admin user is created on first run (can be overridden via env vars).
