"""
Flask Dashboard – Hiển thị kết quả Daily Check theo thời gian thực và lịch sử.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, request, abort

from src.config_manager import get, load_config
from src.dynamic_modules import DynamicModuleStore, get_module_templates, build_module_from_template
from src.logger import ResultStore
from src import scheduler as sched_module

load_config()

BASE_DIR = Path(__file__).resolve().parent.parent

app = Flask(
    __name__,
    template_folder="templates",
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/static",
)
app.secret_key = get("dashboard.secret_key", "dev-secret-key")

logger = logging.getLogger("dailycheck.dashboard")
store = ResultStore()
module_store = DynamicModuleStore()

STATUS_COLOR = {
    "OK": "success",
    "WARNING": "warning",
    "CRITICAL": "danger",
    "ERROR": "secondary",
}


@app.route("/")
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
def run_detail(run_id: str):
    results = store.get_run_results(run_id)
    run_info = store.get_run(run_id)
    # Nhóm theo system
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


def _module_cards():
    cards = []
    for module in module_store.list_modules():
        job = sched_module.get_dynamic_job(module.id)
        last_run = module_store.get_module_last_run(module.id)
        cards.append({
            **_module_to_dict(module),
            "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
            "is_running": sched_module.is_module_running(module.id),
            "last_run": last_run,
        })
    return cards


@app.route("/modules")
def modules_index():
    return render_template(
        "modules.html",
        modules=_module_cards(),
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


@app.route("/modules/new")
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
def modules_edit(module_id: int):
    try:
        module = module_store.get_module(module_id)
    except KeyError:
        abort(404)
    return render_template(
        "module_form.html",
        module=module,
        module_json=json.dumps(_module_to_dict(module)),
        module_templates=json.dumps(get_module_templates()),
        module_runs=module_store.get_module_runs(module_id),
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        default_timezone=get("general.timezone", "Asia/Ho_Chi_Minh"),
    )


@app.route("/api/trigger", methods=["POST"])
def api_trigger():
    """Manual trigger Daily Check."""
    if sched_module.is_running():
        return jsonify({"status": "already_running", "message": "Daily Check đang chạy"}), 409
    msg = sched_module.trigger_now()
    return jsonify({"status": "triggered", "message": msg})


@app.route("/api/modules", methods=["GET"])
def api_modules():
    return jsonify(_module_cards())


@app.route("/api/module-templates", methods=["GET"])
def api_module_templates():
    return jsonify(get_module_templates())


@app.route("/api/module-templates/<template_key>", methods=["GET"])
def api_module_template_detail(template_key: str):
    try:
        return jsonify(build_module_from_template(template_key))
    except KeyError:
        abort(404)


@app.route("/api/modules", methods=["POST"])
def api_modules_create():
    payload = request.get_json(force=True)
    try:
        module = module_store.create_module(payload)
        sched_module.resync_dynamic_jobs()
        return jsonify(_module_to_dict(module)), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/modules/<int:module_id>", methods=["GET"])
def api_module_detail(module_id: int):
    try:
        return jsonify(_module_to_dict(module_store.get_module(module_id)))
    except KeyError:
        abort(404)


@app.route("/api/modules/<int:module_id>", methods=["PUT"])
def api_module_update(module_id: int):
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
def api_module_delete(module_id: int):
    try:
        module_store.get_module(module_id)
    except KeyError:
        abort(404)
    module_store.delete_module(module_id)
    sched_module.resync_dynamic_jobs()
    return jsonify({"status": "deleted"})


@app.route("/api/modules/<int:module_id>/trigger", methods=["POST"])
def api_module_trigger(module_id: int):
    try:
        module_store.get_module(module_id)
    except KeyError:
        abort(404)
    if sched_module.is_module_running(module_id):
        return jsonify({"status": "already_running", "message": "Module đang chạy"}), 409
    msg = sched_module.trigger_dynamic_module_now(module_id)
    return jsonify({"status": "triggered", "message": msg})


@app.route("/api/modules/<int:module_id>/runs", methods=["GET"])
def api_module_runs(module_id: int):
    return jsonify(module_store.get_module_runs(module_id))


@app.route("/api/status")
def api_status():
    """API trả về trạng thái hiện tại."""
    latest = store.get_latest_run()
    return jsonify({
        "is_running": sched_module.is_running(),
        "latest_run": latest,
        "server_time": datetime.now().isoformat(),
    })


@app.route("/api/runs")
def api_runs():
    runs = store.get_recent_runs(30)
    return jsonify(runs)


@app.route("/api/run/<run_id>")
def api_run_detail(run_id: str):
    results = store.get_run_results(run_id)
    return jsonify(results)


@app.route("/report/<run_id>")
def view_report(run_id: str):
    """Xem báo cáo HTML đã lưu."""
    reports_dir = Path(get("general.data_dir", "data")) / "reports"
    report_path = reports_dir / f"report_{run_id}.html"
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")
    return "Báo cáo không tìm thấy", 404


def create_app():
    return app


if __name__ == "__main__":
    host = get("dashboard.host", "0.0.0.0")
    port = get("dashboard.port", 5000)
    debug = get("dashboard.debug", False)
    app.run(host=host, port=port, debug=debug)
