"""
Flask Dashboard – Daily Check results, schedule, and dynamic module management.

Authentication
--------------
All pages require login.  Two roles are supported:
  admin – full access (built-in checks, scheduler trigger, user management,
          all dynamic modules).
  user  – can create dynamic modules and interact only with their own modules.
          The built-in check dashboard and scheduler trigger are hidden/blocked.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    Flask, render_template, jsonify, request, abort,
    redirect, url_for, flash, session,
)

from src.config_manager import get, load_config
from src.dynamic_modules import DynamicModuleStore, get_module_templates, build_module_from_template
from src.logger import ResultStore
from src import scheduler as sched_module
import src.auth as auth

load_config()

BASE_DIR = Path(__file__).resolve().parent.parent

app = Flask(
    __name__,
    template_folder="templates",
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/static",
)
app.secret_key = get("dashboard.secret_key", "dev-secret-key")
app.permanent_session_lifetime = timedelta(hours=8)

logger = logging.getLogger("dailycheck.dashboard")
store = ResultStore()
module_store = DynamicModuleStore()

# Initialise the auth DB (creates users table + default admin if needed).
auth.init_auth_db()

STATUS_COLOR = {
    "OK": "success",
    "WARNING": "warning",
    "CRITICAL": "danger",
    "ERROR": "secondary",
}


# ── Context processor – inject current user into every template ──────────────

@app.context_processor
def inject_user():
    user = auth.get_current_user()
    return {
        "current_user": user,
        "is_admin": auth.is_admin(),
    }


# ── Error handlers ───────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template(
        "error.html",
        code=403,
        message="Bạn không có quyền truy cập trang này.",
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    ), 403


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(_post_login_url())
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = auth.verify_password(username, password)
        if user:
            auth.login_user(user)
            logger.info("User '%s' logged in.", user.username)
            next_url = request.args.get("next") or _post_login_url()
            return redirect(next_url)
        error = "Tên đăng nhập hoặc mật khẩu không đúng."
    return render_template(
        "login.html",
        error=error,
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


@app.route("/logout")
def logout():
    username = session.get("username", "unknown")
    auth.logout_user()
    logger.info("User '%s' logged out.", username)
    return redirect(url_for("login"))


def _post_login_url():
    """Redirect admins to dashboard, regular users to modules page."""
    if auth.is_admin():
        return url_for("index")
    return url_for("modules_index")


# ── Built-in check dashboard (admin only) ────────────────────────────────────

@app.route("/")
@auth.login_required
@auth.admin_required
def index():
    runs = store.get_recent_runs(30)
    latest = runs[0] if runs else None
    latest_results = []
    modules = _module_cards()
    if latest:
        latest_results = store.get_run_results(latest["run_id"])
    return render_template(
        "index.html",
        runs=runs,
        latest=latest,
        latest_results=latest_results,
        modules=modules,
        status_color=STATUS_COLOR,
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        is_running=sched_module.is_running(),
    )


@app.route("/run/<run_id>")
@auth.login_required
def run_detail(run_id: str):
    run_info = _assert_run_access(run_id)
    results = store.get_run_results(run_id)
    systems: dict = {}
    for r in results:
        systems.setdefault(r["system"], []).append(r)
    return render_template(
        "run_detail.html",
        run_info=run_info,
        systems=systems,
        status_color=STATUS_COLOR,
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


# ── User management (admin only) ─────────────────────────────────────────────

@app.route("/users")
@auth.login_required
@auth.admin_required
def users_index():
    users = auth.list_users()
    return render_template(
        "users.html",
        users=users,
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


@app.route("/api/users", methods=["GET"])
@auth.login_required
@auth.admin_required
def api_users():
    users = auth.list_users()
    return jsonify([
        {
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "role": u.role,
            "enabled": u.enabled,
        }
        for u in users
    ])


@app.route("/api/users", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_users_create():
    data = request.get_json(force=True)
    try:
        user = auth.create_user(
            username=str(data.get("username", "")).strip(),
            password=str(data.get("password", "")),
            role=str(data.get("role", "user")),
            display_name=str(data.get("display_name", "")).strip(),
        )
        return jsonify({
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "enabled": user.enabled,
        }), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/users/<int:user_id>", methods=["PUT"])
@auth.login_required
@auth.admin_required
def api_user_update(user_id: int):
    data = request.get_json(force=True)
    # Prevent disabling the only admin
    target = auth.get_user_by_id(user_id)
    if not target:
        abort(404)
    enabled = data.get("enabled")
    if enabled is False and target.role == "admin":
        all_admins = [u for u in auth.list_users() if u.role == "admin" and u.enabled]
        if len(all_admins) <= 1:
            return jsonify({"error": "Không thể vô hiệu hóa admin cuối cùng."}), 400
    try:
        user = auth.update_user(
            user_id,
            display_name=data.get("display_name"),
            role=data.get("role"),
            enabled=enabled,
            password=data.get("password") or None,
        )
        return jsonify({
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "enabled": user.enabled,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@auth.login_required
@auth.admin_required
def api_user_delete(user_id: int):
    # Prevent self-deletion
    if user_id == session.get("user_id"):
        return jsonify({"error": "Không thể xóa chính mình."}), 400
    target = auth.get_user_by_id(user_id)
    if not target:
        abort(404)
    # Prevent deleting the last admin
    if target.role == "admin":
        all_admins = [u for u in auth.list_users() if u.role == "admin"]
        if len(all_admins) <= 1:
            return jsonify({"error": "Không thể xóa admin cuối cùng."}), 400
    auth.delete_user(user_id)
    return jsonify({"status": "deleted"})


# ── Change own password ───────────────────────────────────────────────────────

@app.route("/api/users/me/password", methods=["POST"])
@auth.login_required
def api_change_password():
    data = request.get_json(force=True)
    user_id = session.get("user_id")
    current_pw = str(data.get("current_password", ""))
    new_pw = str(data.get("new_password", ""))
    if not new_pw:
        return jsonify({"error": "Mật khẩu mới không được trống."}), 400
    # Verify current password
    user = auth.get_user_by_id(user_id)
    if not auth.verify_password(user.username, current_pw):
        return jsonify({"error": "Mật khẩu hiện tại không đúng."}), 400
    auth.update_user(user_id, password=new_pw)
    return jsonify({"status": "ok"})


# ── Dynamic module helpers ────────────────────────────────────────────────────

def _module_to_dict(module):
    return {
        "id": module.id,
        "name": module.name,
        "system_name": module.system_name,
        "template_key": module.template_key,
        "enabled": module.enabled,
        "timezone": module.timezone,
        "daily_time": module.daily_time,
        "recipient_emails": module.recipient_emails,
        "created_by": module.created_by,
        "hosts": [
            {
                "id": host.id,
                "name": host.name,
                "ip": host.ip,
                "port": host.port,
                "username": host.username,
                "auth_type": host.auth_type,
                "key_path": host.key_path,
                "has_password": bool(host.encrypted_password),
                "variables": host.variables,
            }
            for host in module.hosts
        ],
        "steps": [
            {
                "id": step.id,
                "name": step.name,
                "command": step.command,
                "timeout_sec": step.timeout_sec,
                "run_on_each_host": step.run_on_each_host,
                "rules": step.rules,
            }
            for step in module.steps
        ],
        "created_at": module.created_at,
        "updated_at": module.updated_at,
    }


def _visible_modules():
    """Return modules visible to the current user."""
    if auth.is_admin():
        return module_store.list_modules()
    return module_store.list_modules(created_by=auth.current_username())


def _module_cards():
    cards = []
    for module in _visible_modules():
        job = sched_module.get_dynamic_job(module.id)
        last_run = module_store.get_module_last_run(module.id)
        cards.append({
            **_module_to_dict(module),
            "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
            "is_running": sched_module.is_module_running(module.id),
            "last_run": last_run,
        })
    return cards


def _assert_module_access(module_id: int):
    """Raise 404 if not found, 403 if regular user doesn't own it."""
    try:
        module = module_store.get_module(module_id)
    except KeyError:
        abort(404)
    if not auth.is_admin() and module.created_by.lower() != auth.current_username().lower():
        abort(403)
    return module


def _assert_run_access(run_id: str):
    """Raise 404 if run is missing, 403 if current user cannot access it."""
    run_info = store.get_run(run_id)
    if not run_info:
        abort(404)
    if run_info.get("run_type") == "builtin":
        if not auth.is_admin():
            abort(403)
        return run_info
    module_id = run_info.get("module_id")
    if module_id is None:
        if not auth.is_admin():
            abort(403)
        return run_info
    _assert_module_access(int(module_id))
    return run_info


# ── Dynamic module UI routes ─────────────────────────────────────────────────

@app.route("/modules")
@auth.login_required
def modules_index():
    return render_template(
        "modules.html",
        modules=_module_cards(),
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


@app.route("/modules/new")
@auth.login_required
def modules_new():
    return render_template(
        "module_form.html",
        module_json=json.dumps(None),
        module_templates=json.dumps(get_module_templates()),
        module=None,
        module_runs=[],
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        default_timezone=get("general.timezone", "Asia/Ho_Chi_Minh"),
    )


@app.route("/modules/<int:module_id>/edit")
@auth.login_required
def modules_edit(module_id: int):
    module = _assert_module_access(module_id)
    return render_template(
        "module_form.html",
        module=module,
        module_json=json.dumps(_module_to_dict(module)),
        module_templates=json.dumps(get_module_templates()),
        module_runs=module_store.get_module_runs(module_id),
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        default_timezone=get("general.timezone", "Asia/Ho_Chi_Minh"),
    )


# ── Dynamic module API ────────────────────────────────────────────────────────

@app.route("/api/modules", methods=["GET"])
@auth.login_required
def api_modules():
    return jsonify(_module_cards())


@app.route("/api/module-templates", methods=["GET"])
@auth.login_required
def api_module_templates():
    return jsonify(get_module_templates())


@app.route("/api/module-templates/<template_key>", methods=["GET"])
@auth.login_required
def api_module_template_detail(template_key: str):
    try:
        return jsonify(build_module_from_template(template_key))
    except KeyError:
        abort(404)


@app.route("/api/modules", methods=["POST"])
@auth.login_required
def api_modules_create():
    payload = request.get_json(force=True)
    try:
        module = module_store.create_module(payload, created_by=auth.current_username())
        sched_module.resync_dynamic_jobs()
        return jsonify(_module_to_dict(module)), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/modules/<int:module_id>", methods=["GET"])
@auth.login_required
def api_module_detail(module_id: int):
    module = _assert_module_access(module_id)
    return jsonify(_module_to_dict(module))


@app.route("/api/modules/<int:module_id>", methods=["PUT"])
@auth.login_required
def api_module_update(module_id: int):
    _assert_module_access(module_id)
    payload = request.get_json(force=True)
    try:
        module = module_store.update_module(module_id, payload)
        sched_module.resync_dynamic_jobs()
        return jsonify(_module_to_dict(module))
    except KeyError:
        abort(404)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/modules/<int:module_id>", methods=["DELETE"])
@auth.login_required
def api_module_delete(module_id: int):
    _assert_module_access(module_id)
    module_store.delete_module(module_id)
    sched_module.resync_dynamic_jobs()
    return jsonify({"status": "deleted"})


@app.route("/api/modules/<int:module_id>/trigger", methods=["POST"])
@auth.login_required
def api_module_trigger(module_id: int):
    _assert_module_access(module_id)
    if sched_module.is_module_running(module_id):
        return jsonify({"status": "already_running", "message": "Module đang chạy"}), 409
    msg = sched_module.trigger_dynamic_module_now(module_id)
    return jsonify({"status": "triggered", "message": msg})


@app.route("/api/modules/<int:module_id>/runs", methods=["GET"])
@auth.login_required
def api_module_runs(module_id: int):
    _assert_module_access(module_id)
    return jsonify(module_store.get_module_runs(module_id))


# ── Built-in scheduler trigger (admin only) ───────────────────────────────────

@app.route("/api/trigger", methods=["POST"])
@auth.login_required
@auth.admin_required
def api_trigger():
    """Manual trigger for the built-in Daily Check."""
    if sched_module.is_running():
        return jsonify({"status": "already_running", "message": "Daily Check đang chạy"}), 409
    msg = sched_module.trigger_now()
    return jsonify({"status": "triggered", "message": msg})


# ── Read-only status APIs (admin only) ───────────────────────────────────────

@app.route("/api/status")
@auth.login_required
@auth.admin_required
def api_status():
    latest = store.get_latest_run()
    return jsonify({
        "is_running": sched_module.is_running(),
        "latest_run": latest,
        "server_time": datetime.now().isoformat(),
    })


@app.route("/api/runs")
@auth.login_required
@auth.admin_required
def api_runs():
    return jsonify(store.get_recent_runs(30))


@app.route("/api/run/<run_id>")
@auth.login_required
def api_run_detail(run_id: str):
    _assert_run_access(run_id)
    return jsonify(store.get_run_results(run_id))


@app.route("/report/<run_id>")
@auth.login_required
def view_report(run_id: str):
    """View a saved HTML report."""
    _assert_run_access(run_id)
    reports_dir = Path(get("general.data_dir", "data")) / "reports"
    report_path = reports_dir / f"report_{run_id}.html"
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")
    return "Báo cáo không tìm thấy", 404


# ── Healthcheck (no auth required – for Docker / load balancer) ──────────────

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "server_time": datetime.now().isoformat()})


def create_app():
    return app


if __name__ == "__main__":
    host = get("dashboard.host", "0.0.0.0")
    port = get("dashboard.port", 5000)
    debug = get("dashboard.debug", False)
    app.run(host=host, port=port, debug=debug)
