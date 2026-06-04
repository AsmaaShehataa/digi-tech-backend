from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import date, datetime
from functools import wraps
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "admin_dashboard.db"

ALLOWED_STATUSES = {"planned", "in_progress", "on_hold", "completed", "cancelled"}
ALLOWED_CURRENCIES = {"USD", "EGP"}
CURRENCY_SYMBOLS = {"USD": "$", "EGP": "E£"}
ALLOWED_DEPLOY_TARGETS = {"public", "admin_internal"}

DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@digi-tech.local").strip().lower()
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")
DEFAULT_ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH") or generate_password_hash(DEFAULT_ADMIN_PASSWORD)
APP_DEPLOY_TARGET = os.environ.get("APP_DEPLOY_TARGET", "public").strip().lower()
if APP_DEPLOY_TARGET not in ALLOWED_DEPLOY_TARGETS:
    APP_DEPLOY_TARGET = "public"
ADMIN_MODULE_ENABLED = APP_DEPLOY_TARGET == "admin_internal"

_raw_public_api_allowed_origins = os.environ.get("PUBLIC_API_ALLOWED_ORIGINS", "*").strip()
if not _raw_public_api_allowed_origins:
    _raw_public_api_allowed_origins = "*"
if _raw_public_api_allowed_origins == "*":
    PUBLIC_API_ALLOWED_ORIGINS = {"*"}
else:
    PUBLIC_API_ALLOWED_ORIGINS = {
        origin.strip().rstrip("/") for origin in _raw_public_api_allowed_origins.split(",") if origin.strip()
    }
PUBLIC_API_ALLOW_CREDENTIALS = os.environ.get("PUBLIC_API_ALLOW_CREDENTIALS", "0").strip() == "1"

_raw_allowed_admin_ips = os.environ.get("ADMIN_ALLOWED_IPS", "").strip()
ADMIN_ALLOWED_IP_NETWORKS = []
for raw_entry in [entry.strip() for entry in _raw_allowed_admin_ips.split(",") if entry.strip()]:
    try:
        ADMIN_ALLOWED_IP_NETWORKS.append(ip_network(raw_entry, strict=False))
    except ValueError:
        # Invalid entries are ignored intentionally so bad config doesn't crash the app.
        continue


def _today() -> date:
    return date.today()


def _parse_date(raw_value: str) -> date:
    return datetime.strptime(raw_value, "%Y-%m-%d").date()


def _coerce_amount(value: Any) -> float:
    return round(float(value), 2)


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip()
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    return status


def _normalize_currency(value: Any) -> str:
    currency = str(value or "").strip().upper()
    if currency not in ALLOWED_CURRENCIES:
        raise ValueError(f"Invalid currency: {currency}. Allowed currencies: USD, EGP.")
    return currency


def _sanitize_milestones(raw_milestones: Any, start_date: str, deadline: str, total_price: float) -> list[dict[str, Any]]:
    milestones: list[dict[str, Any]] = []
    if not raw_milestones:
        return milestones
    if not isinstance(raw_milestones, list):
        raise ValueError("Milestones must be an array.")

    start = _parse_date(start_date)
    end = _parse_date(deadline)
    amount_total = 0.0
    for milestone in raw_milestones:
        title = str(milestone.get("title", "")).strip()
        due_date_raw = str(milestone.get("due_date", "")).strip()
        if not title or not due_date_raw:
            continue
        due_date = _parse_date(due_date_raw)
        if due_date < start or due_date > end:
            raise ValueError("Milestone due date must be between project start date and deadline.")
        amount = _coerce_amount(milestone.get("amount", 0))
        if amount < 0:
            raise ValueError("Milestone amount must be positive.")
        amount_total += amount
        milestones.append(
            {
                "title": title,
                "amount": amount,
                "due_date": due_date_raw,
                "paid": bool(milestone.get("paid", False)),
            }
        )
    if amount_total > total_price:
        raise ValueError("Milestone amount total cannot exceed total project price.")
    milestones.sort(key=lambda item: item["due_date"])
    return milestones


def _compute_project_metrics(project: dict[str, Any]) -> dict[str, Any]:
    total = float(project["total_price"])
    paid = float(project["paid_amount"])
    remaining = max(total - paid, 0)
    payment_progress = 0 if total <= 0 else min((paid / total) * 100, 100)

    deadline = _parse_date(project["deadline"])
    days_remaining = (deadline - _today()).days
    deadline_state = "overdue" if days_remaining < 0 else "upcoming" if days_remaining <= 14 else "on_track"

    overdue_milestones_count = 0
    overdue_milestones_amount = 0.0
    pending_milestones_count = 0
    pending_milestones_amount = 0.0
    next_due_milestone = None
    today = _today()

    unpaid = [m for m in project["milestones"] if not m["paid"]]
    for milestone in unpaid:
        due = _parse_date(milestone["due_date"])
        if due < today:
            overdue_milestones_count += 1
            overdue_milestones_amount += float(milestone["amount"])
        else:
            pending_milestones_count += 1
            pending_milestones_amount += float(milestone["amount"])

    if unpaid:
        next_due_milestone = sorted(unpaid, key=lambda item: item["due_date"])[0]

    effective_status = project["status"]
    if remaining <= 0 and effective_status != "cancelled":
        effective_status = "completed"

    return {
        "remaining_balance": round(remaining, 2),
        "payment_progress": round(payment_progress, 2),
        "days_remaining": days_remaining,
        "deadline_state": deadline_state,
        "pending_milestones_count": pending_milestones_count,
        "pending_milestones_amount": round(pending_milestones_amount, 2),
        "overdue_milestones_count": overdue_milestones_count,
        "overdue_milestones_amount": round(overdue_milestones_amount, 2),
        "next_due_milestone": next_due_milestone,
        "effective_status": effective_status,
    }


class DashboardRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_name TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    total_price REAL NOT NULL,
                    paid_amount REAL NOT NULL DEFAULT 0,
                    start_date TEXT NOT NULL,
                    deadline TEXT NOT NULL,
                    status TEXT NOT NULL,
                    notes TEXT,
                    milestones_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS admin_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS inquiries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    company TEXT,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            existing = conn.execute("SELECT id FROM admin_users WHERE email = ?", (DEFAULT_ADMIN_EMAIL,)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO admin_users (email, password_hash, is_active) VALUES (?, ?, 1)",
                    (DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD_HASH),
                )

    def authenticate_admin(self, email: str, password: str) -> dict[str, Any] | None:
        email = email.strip().lower()
        if not email or not password:
            return None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, email, password_hash, is_active FROM admin_users WHERE email = ?",
                (email,),
            ).fetchone()
            if row is None or not bool(row["is_active"]):
                return None
            if not check_password_hash(row["password_hash"], password):
                return None
            conn.execute("UPDATE admin_users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            return {"id": row["id"], "email": row["email"]}

    def _serialize_project(self, row: sqlite3.Row) -> dict[str, Any]:
        milestones = json.loads(row["milestones_json"] or "[]")
        project = {
            "id": row["id"],
            "client_name": row["client_name"],
            "project_name": row["project_name"],
            "currency": row["currency"],
            "total_price": round(float(row["total_price"]), 2),
            "paid_amount": round(float(row["paid_amount"]), 2),
            "start_date": row["start_date"],
            "deadline": row["deadline"],
            "status": row["status"],
            "notes": row["notes"],
            "milestones": milestones,
        }
        project["metrics"] = _compute_project_metrics(project)
        return project

    def list_projects(self, currency_filter: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT id, client_name, project_name, currency, total_price, paid_amount, start_date, deadline, status, notes, milestones_json
            FROM projects
        """
        params: tuple[Any, ...] = ()
        if currency_filter:
            query += " WHERE currency = ?"
            params = (currency_filter,)
        query += " ORDER BY deadline ASC, id DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._serialize_project(row) for row in rows]

    def get_project(self, project_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, client_name, project_name, currency, total_price, paid_amount, start_date, deadline, status, notes, milestones_json
                FROM projects
                WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Project not found")
        return self._serialize_project(row)

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_name = str(payload["client_name"]).strip()
        project_name = str(payload["project_name"]).strip()
        currency = _normalize_currency(payload.get("currency", "USD"))
        status = _normalize_status(payload["status"])
        total_price = _coerce_amount(payload["total_price"])
        paid_amount = _coerce_amount(payload.get("paid_amount", 0))
        start_date = str(payload["start_date"]).strip()
        deadline = str(payload["deadline"]).strip()
        notes = str(payload.get("notes", "")).strip() or None

        start = _parse_date(start_date)
        end = _parse_date(deadline)
        if end < start:
            raise ValueError("Deadline cannot be before start date.")
        if paid_amount > total_price:
            raise ValueError("Paid amount cannot exceed total price.")

        milestones = _sanitize_milestones(payload.get("milestones", []), start_date, deadline, total_price)

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO projects (
                    client_name, project_name, currency, total_price, paid_amount, start_date, deadline, status, notes, milestones_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_name,
                    project_name,
                    currency,
                    total_price,
                    paid_amount,
                    start_date,
                    deadline,
                    status,
                    notes,
                    json.dumps(milestones),
                ),
            )
            project_id = cur.lastrowid
        return self.get_project(project_id)

    def update_project(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_project(project_id)
        merged = {
            "client_name": payload.get("client_name", existing["client_name"]),
            "project_name": payload.get("project_name", existing["project_name"]),
            "currency": payload.get("currency", existing["currency"]),
            "total_price": payload.get("total_price", existing["total_price"]),
            "paid_amount": payload.get("paid_amount", existing["paid_amount"]),
            "start_date": payload.get("start_date", existing["start_date"]),
            "deadline": payload.get("deadline", existing["deadline"]),
            "status": payload.get("status", existing["status"]),
            "notes": payload.get("notes", existing["notes"] or ""),
            "milestones": payload.get("milestones", existing["milestones"]),
        }

        # re-validate merged payload by reusing create rules
        client_name = str(merged["client_name"]).strip()
        project_name = str(merged["project_name"]).strip()
        currency = _normalize_currency(merged["currency"])
        status = _normalize_status(merged["status"])
        total_price = _coerce_amount(merged["total_price"])
        paid_amount = _coerce_amount(merged["paid_amount"])
        start_date = str(merged["start_date"]).strip()
        deadline = str(merged["deadline"]).strip()
        notes = str(merged.get("notes", "")).strip() or None
        _parse_date(start_date)
        _parse_date(deadline)
        if _parse_date(deadline) < _parse_date(start_date):
            raise ValueError("Deadline cannot be before start date.")
        if paid_amount > total_price:
            raise ValueError("Paid amount cannot exceed total price.")

        milestones = _sanitize_milestones(merged.get("milestones", []), start_date, deadline, total_price)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE projects
                SET client_name = ?, project_name = ?, currency = ?, total_price = ?, paid_amount = ?, start_date = ?,
                    deadline = ?, status = ?, notes = ?, milestones_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    client_name,
                    project_name,
                    currency,
                    total_price,
                    paid_amount,
                    start_date,
                    deadline,
                    status,
                    notes,
                    json.dumps(milestones),
                    project_id,
                ),
            )
        return self.get_project(project_id)

    def delete_project(self, project_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def create_inquiry(self, payload: dict[str, Any]) -> dict[str, Any]:
        full_name = str(payload.get("full_name", "")).strip()
        email = str(payload.get("email", "")).strip().lower()
        company = str(payload.get("company", "")).strip() or None
        message = str(payload.get("message", "")).strip()

        if not full_name or not email or not message:
            raise ValueError("full_name, email, and message are required.")

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO inquiries (full_name, email, company, message)
                VALUES (?, ?, ?, ?)
                """,
                (full_name, email, company, message),
            )
            inquiry_id = cur.lastrowid
            row = conn.execute(
                """
                SELECT id, full_name, email, company, message, created_at
                FROM inquiries
                WHERE id = ?
                """,
                (inquiry_id,),
            ).fetchone()
        return dict(row)


def _build_overview(projects: list[dict[str, Any]], currency: str | None) -> dict[str, Any]:
    totals = {
        "total_projects": len(projects),
        "active_projects": 0,
        "completed_projects": 0,
        "pending_payments_count": 0,
        "pending_payments_amount": 0.0,
        "overdue_payments_count": 0,
        "overdue_payments_amount": 0.0,
        "upcoming_deadlines_count": 0,
        "total_contract_value": 0.0,
        "total_paid": 0.0,
        "total_remaining": 0.0,
        "portfolio_payment_progress": 0.0,
    }
    upcoming_deadlines: list[dict[str, Any]] = []

    for project in projects:
        metrics = project["metrics"]
        totals["total_contract_value"] += project["total_price"]
        totals["total_paid"] += project["paid_amount"]
        totals["total_remaining"] += metrics["remaining_balance"]

        if metrics["effective_status"] == "completed":
            totals["completed_projects"] += 1
        elif metrics["effective_status"] != "cancelled":
            totals["active_projects"] += 1

        totals["pending_payments_count"] += metrics["pending_milestones_count"]
        totals["pending_payments_amount"] += metrics["pending_milestones_amount"]
        totals["overdue_payments_count"] += metrics["overdue_milestones_count"]
        totals["overdue_payments_amount"] += metrics["overdue_milestones_amount"]

        if 0 <= metrics["days_remaining"] <= 14 and metrics["effective_status"] != "completed":
            totals["upcoming_deadlines_count"] += 1
            upcoming_deadlines.append(
                {
                    "project_id": project["id"],
                    "project_name": project["project_name"],
                    "client_name": project["client_name"],
                    "deadline": project["deadline"],
                    "days_remaining": metrics["days_remaining"],
                }
            )

    if totals["total_contract_value"] > 0:
        totals["portfolio_payment_progress"] = round((totals["total_paid"] / totals["total_contract_value"]) * 100, 2)

    for key in ("pending_payments_amount", "overdue_payments_amount", "total_contract_value", "total_paid", "total_remaining"):
        totals[key] = round(totals[key], 2)

    return {"currency": currency, "totals": totals, "upcoming_deadlines": upcoming_deadlines}


def _serialize_csv(projects: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Project ID",
            "Client Name",
            "Project Name",
            "Currency",
            "Status",
            "Start Date",
            "Deadline",
            "Total Price",
            "Paid Amount",
            "Remaining Balance",
            "Payment Progress (%)",
            "Pending Milestones",
            "Overdue Milestones",
        ]
    )
    for project in projects:
        metrics = project["metrics"]
        writer.writerow(
            [
                project["id"],
                project["client_name"],
                project["project_name"],
                project["currency"],
                metrics["effective_status"],
                project["start_date"],
                project["deadline"],
                f"{project['total_price']:.2f}",
                f"{project['paid_amount']:.2f}",
                f"{metrics['remaining_balance']:.2f}",
                f"{metrics['payment_progress']:.2f}",
                metrics["pending_milestones_count"],
                metrics["overdue_milestones_count"],
            ]
        )
    return output.getvalue()


repo = DashboardRepository(DB_PATH)
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")


def _is_logged_in() -> bool:
    return bool(session.get("admin_user_id"))


def _resolve_public_cors_origin() -> str | None:
    request_origin = request.headers.get("Origin", "").strip().rstrip("/")
    if "*" in PUBLIC_API_ALLOWED_ORIGINS:
        return request_origin if request_origin and PUBLIC_API_ALLOW_CREDENTIALS else "*"
    if request_origin and request_origin in PUBLIC_API_ALLOWED_ORIGINS:
        return request_origin
    return None


def _append_vary_header(response: Response, header_name: str) -> None:
    existing = response.headers.get("Vary", "")
    vary_values = [item.strip() for item in existing.split(",") if item.strip()]
    if header_name not in vary_values:
        vary_values.append(header_name)
        response.headers["Vary"] = ", ".join(vary_values)


@app.after_request
def _apply_public_api_cors(response: Response) -> Response:
    if request.path.startswith("/api/public/"):
        origin = _resolve_public_cors_origin()
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            if origin != "*":
                _append_vary_header(response, "Origin")
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        if PUBLIC_API_ALLOW_CREDENTIALS:
            response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


def _safe_next_url(raw_next: str | None) -> str:
    if raw_next and raw_next.startswith("/") and not raw_next.startswith("//"):
        return raw_next
    return url_for("admin_dashboard")


def _extract_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _is_admin_ip_allowed() -> bool:
    if not ADMIN_ALLOWED_IP_NETWORKS:
        return True
    client_ip_raw = _extract_client_ip()
    try:
        client_ip = ip_address(client_ip_raw)
    except ValueError:
        return False
    return any(client_ip in network for network in ADMIN_ALLOWED_IP_NETWORKS)


def _require_admin_web(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not ADMIN_MODULE_ENABLED:
            abort(404)
        if not _is_admin_ip_allowed():
            abort(403)
        if not _is_logged_in():
            return redirect(url_for("admin_login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def _require_admin_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not ADMIN_MODULE_ENABLED:
            return jsonify({"error": "Not found"}), 404
        if not _is_admin_ip_allowed():
            return jsonify({"error": "Forbidden"}), 403
        if not _is_logged_in():
            return jsonify({"error": "Authentication required"}), 401
        return view(*args, **kwargs)

    return wrapped


@app.route("/")
def home() -> Response:
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/<path:asset>")
def serve_asset(asset: str) -> Response:
    blocked_prefixes = ("api/", "admin", "static/", "templates/")
    if asset.startswith(blocked_prefixes):
        abort(404)
    target = BASE_DIR / asset
    if target.exists() and target.is_file():
        return send_from_directory(BASE_DIR, asset)
    abort(404)


@app.route("/api/public/health", methods=["GET", "OPTIONS"])
def public_health() -> Response:
    if request.method == "OPTIONS":
        return Response(status=204)
    return jsonify(
        {
            "status": "ok",
            "app_deploy_target": APP_DEPLOY_TARGET,
            "admin_module_enabled": ADMIN_MODULE_ENABLED,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )


@app.route("/api/public/inquiries", methods=["POST", "OPTIONS"])
def public_create_inquiry() -> Response:
    if request.method == "OPTIONS":
        return Response(status=204)
    payload = request.get_json(silent=True) or {}
    try:
        inquiry = repo.create_inquiry(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"inquiry": inquiry}), 201


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login() -> str | Response:
    if not ADMIN_MODULE_ENABLED:
        abort(404)
    if not _is_admin_ip_allowed():
        abort(403)
    if _is_logged_in():
        return redirect(url_for("admin_dashboard"))

    error = ""
    prefill_email = DEFAULT_ADMIN_EMAIL
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        prefill_email = email or prefill_email
        user = repo.authenticate_admin(email, password)
        if user:
            session.clear()
            session["admin_user_id"] = user["id"]
            session["admin_email"] = user["email"]
            return redirect(_safe_next_url(request.args.get("next")))
        error = "Invalid email or password."

    return render_template("admin_login.html", error=error, prefill_email=prefill_email)


@app.route("/admin/logout", methods=["POST"])
@_require_admin_web
def admin_logout() -> Response:
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@_require_admin_web
def admin_dashboard() -> str:
    return render_template("admin_dashboard.html", admin_email=session.get("admin_email"))


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login() -> Response:
    if not ADMIN_MODULE_ENABLED:
        return jsonify({"error": "Not found"}), 404
    if not _is_admin_ip_allowed():
        return jsonify({"error": "Forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    user = repo.authenticate_admin(payload.get("email", ""), payload.get("password", ""))
    if not user:
        return jsonify({"error": "Invalid email or password"}), 401
    session.clear()
    session["admin_user_id"] = user["id"]
    session["admin_email"] = user["email"]
    return jsonify({"message": "Logged in successfully", "admin_email": user["email"]})


@app.route("/api/admin/logout", methods=["POST"])
@_require_admin_api
def api_admin_logout() -> Response:
    session.clear()
    return jsonify({"message": "Logged out"})


@app.route("/api/admin/projects", methods=["GET"])
@_require_admin_api
def api_list_projects() -> Response:
    currency = request.args.get("currency")
    currency_filter = _normalize_currency(currency) if currency else None
    projects = repo.list_projects(currency_filter)
    return jsonify({"projects": projects, "currency_filter": currency_filter})


@app.route("/api/admin/projects", methods=["POST"])
@_require_admin_api
def api_create_project() -> Response:
    payload = request.get_json(silent=True) or {}
    required = ["client_name", "project_name", "total_price", "paid_amount", "start_date", "deadline", "status", "currency"]
    missing = [field for field in required if payload.get(field) in (None, "")]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    try:
        project = repo.create_project(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"project": project}), 201


@app.route("/api/admin/projects/<int:project_id>", methods=["PUT"])
@_require_admin_api
def api_update_project(project_id: int) -> Response:
    payload = request.get_json(silent=True) or {}
    try:
        project = repo.update_project(project_id, payload)
    except KeyError:
        return jsonify({"error": "Project not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"project": project})


@app.route("/api/admin/projects/<int:project_id>", methods=["DELETE"])
@_require_admin_api
def api_delete_project(project_id: int) -> Response:
    repo.delete_project(project_id)
    return jsonify({"message": "Project deleted"})


@app.route("/api/admin/overview", methods=["GET"])
@_require_admin_api
def api_overview() -> Response:
    currency = request.args.get("currency")
    currency_filter = _normalize_currency(currency) if currency else None
    projects = repo.list_projects(currency_filter)
    return jsonify(_build_overview(projects, currency_filter))


@app.route("/api/admin/export.csv", methods=["GET"])
@_require_admin_api
def api_export_csv() -> Response:
    currency = request.args.get("currency")
    currency_filter = _normalize_currency(currency) if currency else None
    projects = repo.list_projects(currency_filter)
    payload = _serialize_csv(projects)
    suffix = currency_filter.lower() if currency_filter else "all-currencies"
    return Response(
        payload,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=admin-project-report-{suffix}.csv"},
    )


@app.route("/api/admin/export.json", methods=["GET"])
@_require_admin_api
def api_export_json() -> Response:
    currency = request.args.get("currency")
    currency_filter = _normalize_currency(currency) if currency else None
    projects = repo.list_projects(currency_filter)
    overview = _build_overview(projects, currency_filter)
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "app_deploy_target": APP_DEPLOY_TARGET,
        "currency_filter": currency_filter,
        "overview": overview,
        "projects": projects,
    }
    suffix = currency_filter.lower() if currency_filter else "all-currencies"
    return Response(
        json.dumps(payload, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=admin-project-report-{suffix}.json"},
    )


@app.route("/api/admin/share-report", methods=["POST"])
@_require_admin_api
def api_share_report() -> Response:
    payload = request.get_json(silent=True) or {}
    currency = payload.get("currency")
    currency_filter = _normalize_currency(currency) if currency else None
    projects = repo.list_projects(currency_filter)
    overview = _build_overview(projects, currency_filter)
    totals = overview["totals"]
    symbol = CURRENCY_SYMBOLS.get(currency_filter or "USD", "$")

    client_email = payload.get("client_email", "").strip()
    admin_email = payload.get("admin_email", "").strip()
    if not client_email and not admin_email:
        return jsonify({"error": "Please provide at least one recipient email."}), 400
    recipients = ",".join([email for email in [client_email, admin_email] if email])

    subject = f"Digi-Tech Project & Financial Report ({currency_filter or 'ALL'})"
    body = (
        "Hello,\n\n"
        f"Total projects: {totals['total_projects']}\n"
        f"Active projects: {totals['active_projects']}\n"
        f"Completed projects: {totals['completed_projects']}\n"
        f"Pending payments: {totals['pending_payments_count']} ({symbol}{totals['pending_payments_amount']:.2f})\n"
        f"Overdue payments: {totals['overdue_payments_count']} ({symbol}{totals['overdue_payments_amount']:.2f})\n"
        f"Upcoming deadlines: {totals['upcoming_deadlines_count']}\n"
        f"Portfolio payment progress: {totals['portfolio_payment_progress']}%\n\n"
        "Regards,\nDigi-Tech Admin"
    )
    mailto_link = f"mailto:{recipients}?subject={quote(subject)}&body={quote(body)}"
    return jsonify({"mailto_link": mailto_link, "recipients": recipients, "currency": currency_filter})


if __name__ == "__main__":
    app_port = int(os.environ.get("APP_PORT", "5000"))
    debug_enabled = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_enabled, port=app_port)
