"""
Flask Dashboard – Hiển thị kết quả Daily Check theo thời gian thực và lịch sử.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, redirect, url_for

from src.config_manager import get, load_config
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


def create_app():
    return app


if __name__ == "__main__":
    host = get("dashboard.host", "0.0.0.0")
    port = get("dashboard.port", 5000)
    debug = get("dashboard.debug", False)
    app.run(host=host, port=port, debug=debug)
