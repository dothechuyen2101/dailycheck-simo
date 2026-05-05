"""
Flask Dashboard – Hiển thị kết quả Daily Check và nhập động Systems/Hosts/Plans.
"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, render_template, jsonify, request, redirect, url_for

from src.config_manager import get, load_config
from src.logger import ResultStore
from src import scheduler as sched_module
from src.plan_store import PlanStore
from src.check_registry import (
    get_registry,
    get_step_schema,
    list_system_types,
    validate_step_params,
)
from src.runtime_plan import PlanValidationError, build_runtime_plan
from src.orchestrator import run_daily_check

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
plan_store = PlanStore()

_run_lock = threading.Lock()
_manual_running = False


def _error(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _json_body() -> dict:
    return request.get_json(silent=True) or {}


def _parse_int(value, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} phải là số nguyên")

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
    if latest:
        latest_results = store.get_run_results(latest["run_id"])
    return render_template(
        "index.html",
        runs=runs,
        latest=latest,
        latest_results=latest_results,
        status_color=STATUS_COLOR,
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        is_running=sched_module.is_running(),
    )


@app.route("/run/<run_id>")
def run_detail(run_id: str):
    results = store.get_run_results(run_id)
    runs = store.get_recent_runs(30)
    run_info = next((r for r in runs if r["run_id"] == run_id), None)
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


@app.route("/api/trigger", methods=["POST"])
def api_trigger():
    """Manual trigger Daily Check."""
    if sched_module.is_running():
        return jsonify({"status": "already_running", "message": "Daily Check đang chạy"}), 409
    msg = sched_module.trigger_now()
    return jsonify({"status": "triggered", "message": msg})


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


# ── Pages quản lý cấu hình động ──────────────────────────────────────────

@app.route("/systems")
def page_systems():
    systems = plan_store.list_systems()
    # Kèm số host cho mỗi system
    for s in systems:
        s["hosts_count"] = len(plan_store.list_hosts(s["id"]))
    return render_template(
        "systems.html",
        systems=systems,
        system_types=list_system_types(),
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


@app.route("/systems/<int:system_id>/hosts")
def page_system_hosts(system_id: int):
    system = plan_store.get_system(system_id)
    if not system:
        abort(404)
    hosts = plan_store.list_hosts(system_id)
    return render_template(
        "hosts.html",
        system=system,
        hosts=hosts,
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


@app.route("/plans")
def page_plans():
    plans = plan_store.list_plans()
    for p in plans:
        p["steps_count"] = len(plan_store.list_plan_steps(p["id"]))
    return render_template(
        "plans.html",
        plans=plans,
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


@app.route("/plans/<int:plan_id>/editor")
def page_plan_editor(plan_id: int):
    plan = plan_store.get_plan(plan_id)
    if not plan:
        abort(404)
    steps = plan_store.list_plan_steps(plan_id)
    systems = plan_store.list_systems()
    # Kèm hosts cho mỗi system để chọn scope
    for s in systems:
        s["hosts"] = plan_store.list_hosts(s["id"])
    return render_template(
        "plan_editor.html",
        plan=plan,
        steps=steps,
        systems=systems,
        registry=get_registry(),
        now=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


# ── API Registry ─────────────────────────────────────────────────────────

@app.route("/api/check-types")
def api_check_types():
    return jsonify({
        "system_types": list_system_types(),
        "registry": get_registry(),
    })


# ── API Systems ──────────────────────────────────────────────────────────

@app.route("/api/systems", methods=["GET"])
def api_list_systems():
    return jsonify(plan_store.list_systems())


@app.route("/api/systems", methods=["POST"])
def api_create_system():
    data = _json_body()
    for f in ("name", "system_type"):
        if not data.get(f):
            return _error(f"Thiếu trường '{f}'")
    sid = plan_store.create_system(data)
    return jsonify(plan_store.get_system(sid)), 201


@app.route("/api/systems/<int:system_id>", methods=["GET"])
def api_get_system(system_id: int):
    s = plan_store.get_system(system_id)
    if not s:
        return _error("Không tìm thấy system", 404)
    return jsonify(s)


@app.route("/api/systems/<int:system_id>", methods=["PUT"])
def api_update_system(system_id: int):
    data = _json_body()
    for f in ("name", "system_type"):
        if not data.get(f):
            return _error(f"Thiếu trường '{f}'")
    if not plan_store.update_system(system_id, data):
        return _error("Không tìm thấy system", 404)
    return jsonify(plan_store.get_system(system_id))


@app.route("/api/systems/<int:system_id>", methods=["DELETE"])
def api_delete_system(system_id: int):
    if not plan_store.delete_system(system_id):
        return _error("Không tìm thấy system", 404)
    return jsonify({"deleted": True})


# ── API Hosts ────────────────────────────────────────────────────────────

@app.route("/api/systems/<int:system_id>/hosts", methods=["GET"])
def api_list_hosts(system_id: int):
    if not plan_store.get_system(system_id):
        return _error("Không tìm thấy system", 404)
    return jsonify(plan_store.list_hosts(system_id))


@app.route("/api/systems/<int:system_id>/hosts", methods=["POST"])
def api_create_host(system_id: int):
    if not plan_store.get_system(system_id):
        return _error("Không tìm thấy system", 404)
    data = _json_body()
    for f in ("name", "ip", "username"):
        if not data.get(f):
            return _error(f"Thiếu trường '{f}'")
    hid = plan_store.create_host(system_id, data)
    return jsonify(plan_store.get_host(hid)), 201


@app.route("/api/hosts/<int:host_id>", methods=["PUT"])
def api_update_host(host_id: int):
    data = _json_body()
    for f in ("name", "ip", "username"):
        if not data.get(f):
            return _error(f"Thiếu trường '{f}'")
    if not plan_store.update_host(host_id, data):
        return _error("Không tìm thấy host", 404)
    return jsonify(plan_store.get_host(host_id))


@app.route("/api/hosts/<int:host_id>", methods=["DELETE"])
def api_delete_host(host_id: int):
    if not plan_store.delete_host(host_id):
        return _error("Không tìm thấy host", 404)
    return jsonify({"deleted": True})


# ── API Plans ────────────────────────────────────────────────────────────

@app.route("/api/plans", methods=["GET"])
def api_list_plans():
    return jsonify(plan_store.list_plans())


@app.route("/api/plans", methods=["POST"])
def api_create_plan():
    data = _json_body()
    if not data.get("name"):
        return _error("Thiếu trường 'name'")
    pid = plan_store.create_plan(data)
    return jsonify(plan_store.get_plan(pid)), 201


@app.route("/api/plans/<int:plan_id>", methods=["GET"])
def api_get_plan(plan_id: int):
    p = plan_store.get_plan(plan_id)
    if not p:
        return _error("Không tìm thấy plan", 404)
    p["steps"] = plan_store.list_plan_steps(plan_id)
    return jsonify(p)


@app.route("/api/plans/<int:plan_id>", methods=["PUT"])
def api_update_plan(plan_id: int):
    data = _json_body()
    if not data.get("name"):
        return _error("Thiếu trường 'name'")
    if not plan_store.update_plan(plan_id, data):
        return _error("Không tìm thấy plan", 404)
    return jsonify(plan_store.get_plan(plan_id))


@app.route("/api/plans/<int:plan_id>", methods=["DELETE"])
def api_delete_plan(plan_id: int):
    if not plan_store.delete_plan(plan_id):
        return _error("Không tìm thấy plan", 404)
    return jsonify({"deleted": True})


# ── API Plan Steps ───────────────────────────────────────────────────────

@app.route("/api/plans/<int:plan_id>/steps", methods=["GET"])
def api_list_plan_steps(plan_id: int):
    if not plan_store.get_plan(plan_id):
        return _error("Không tìm thấy plan", 404)
    return jsonify(plan_store.list_plan_steps(plan_id))


def _validate_step_payload(data: dict) -> tuple[dict, list[str]]:
    system_id = data.get("system_id")
    step_id = data.get("step_id")
    if not system_id or not step_id:
        return {}, ["Thiếu system_id hoặc step_id"]
    try:
        system_id = int(system_id)
    except (TypeError, ValueError):
        return {}, ["system_id phải là số nguyên"]
    system = plan_store.get_system(system_id)
    if not system:
        return {}, [f"System id={system_id} không tồn tại"]
    try:
        schema = get_step_schema(system["system_type"], step_id)
    except ValueError as e:
        return {}, [str(e)]

    params = data.get("params") or {}
    advanced_json = data.get("advanced_json")
    if advanced_json:
        if isinstance(advanced_json, str):
            try:
                advanced = json.loads(advanced_json)
            except json.JSONDecodeError as e:
                return {}, [f"Advanced JSON không hợp lệ: {e}"]
        elif isinstance(advanced_json, dict):
            advanced = advanced_json
        else:
            return {}, ["Advanced JSON phải là object JSON"]
        if not isinstance(advanced, dict):
            return {}, ["Advanced JSON phải là object JSON"]
        merged = dict(advanced)
        merged.update(params)
        params = merged

    errors = validate_step_params(system["system_type"], step_id, params)
    if errors:
        return {}, errors

    return {
        "system_id": system_id,
        "step_id": step_id,
        "step_name": data.get("step_name") or schema.get("label"),
        "step_order": int(data.get("step_order") or 0),
        "enabled": bool(data.get("enabled", True)),
        "host_scope": data.get("host_scope") or {"mode": "all"},
        "params": params,
    }, []


@app.route("/api/plans/<int:plan_id>/steps", methods=["POST"])
def api_create_plan_step(plan_id: int):
    if not plan_store.get_plan(plan_id):
        return _error("Không tìm thấy plan", 404)
    payload, errors = _validate_step_payload(_json_body())
    if errors:
        return jsonify({"errors": errors}), 400
    sid = plan_store.create_plan_step(plan_id, payload)
    return jsonify(plan_store.get_plan_step(sid)), 201


@app.route("/api/plan-steps/<int:step_id>", methods=["PUT"])
def api_update_plan_step(step_id: int):
    existing = plan_store.get_plan_step(step_id)
    if not existing:
        return _error("Không tìm thấy step", 404)
    payload, errors = _validate_step_payload(_json_body())
    if errors:
        return jsonify({"errors": errors}), 400
    plan_store.update_plan_step(step_id, payload)
    return jsonify(plan_store.get_plan_step(step_id))


@app.route("/api/plan-steps/<int:step_id>", methods=["DELETE"])
def api_delete_plan_step(step_id: int):
    if not plan_store.delete_plan_step(step_id):
        return _error("Không tìm thấy step", 404)
    return jsonify({"deleted": True})


# ── API Run Plan ─────────────────────────────────────────────────────────

def _run_plan_async(plan_id):
    global _manual_running
    try:
        run_daily_check(plan_id=plan_id)
    except Exception:
        logger.exception("Run plan thất bại")
    finally:
        with _run_lock:
            _manual_running = False


@app.route("/api/run-plan", methods=["POST"])
def api_run_plan():
    global _manual_running
    data = _json_body()
    plan_id = data.get("plan_id")
    try:
        plan_id = int(plan_id) if plan_id is not None else None
    except (TypeError, ValueError):
        return _error("plan_id phải là số nguyên")

    # Validate trước để báo lỗi ngay
    try:
        build_runtime_plan(plan_id=plan_id, store=plan_store)
    except PlanValidationError as e:
        return jsonify({"errors": e.errors}), 400

    with _run_lock:
        if _manual_running or sched_module.is_running():
            return jsonify({"status": "already_running",
                            "message": "Daily Check đang chạy"}), 409
        _manual_running = True

    t = threading.Thread(target=_run_plan_async, args=(plan_id,), daemon=True)
    t.start()
    return jsonify({"status": "triggered", "plan_id": plan_id,
                    "started_at": datetime.now().isoformat()})


def create_app():
    return app


if __name__ == "__main__":
    host = get("dashboard.host", "0.0.0.0")
    port = get("dashboard.port", 5000)
    debug = get("dashboard.debug", False)
    app.run(host=host, port=port, debug=debug)
