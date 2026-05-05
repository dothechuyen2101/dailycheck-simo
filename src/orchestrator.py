"""
Orchestrator – Chạy Daily Check theo runtime plan do Dashboard nhập.
Mọi tham số bước check đều lấy từ SQLite (systems/hosts/plan_steps), không hardcode.
"""
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Optional

from src.config_manager import get
from src.logger import CheckResult, RunSummary, ResultStore
from src.alert_manager import AlertManager
from src.report_generator import generate_html_report, generate_subject
from src.plan_store import PlanStore
from src.runtime_plan import (
    PlanValidationError,
    build_runtime_plan,
    serialize_for_snapshot,
)
from src.checks.runners import run_system_plan

logger = logging.getLogger("dailycheck.orchestrator")


def _overall_status(results: List[CheckResult]) -> str:
    statuses = {r.status for r in results}
    if "CRITICAL" in statuses or "ERROR" in statuses:
        return "CRITICAL"
    if "WARNING" in statuses:
        return "WARNING"
    return "OK"


def run_daily_check(plan_id: Optional[int] = None) -> RunSummary:
    """Entry point chính – chạy Daily Check theo plan."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    started_at = datetime.now().isoformat()
    logger.info(f"=== SIMO Daily Check bắt đầu – Run ID: {run_id} ===")

    store = ResultStore()
    plan_store = PlanStore()
    alert_mgr = AlertManager()
    max_workers = get("general.max_workers", 10)

    summary = RunSummary(run_id=run_id, started_at=started_at)
    store.save_run(summary)

    all_results: List[CheckResult] = []

    try:
        runtime_plan = build_runtime_plan(plan_id=plan_id, store=plan_store)
    except PlanValidationError as e:
        logger.error("Plan không hợp lệ: %s", e)
        for err in e.errors:
            all_results.append(
                CheckResult("Plan", "-", "Validate plan", "ERROR", err)
            )
        plan_store.save_run_snapshot(run_id, plan_id, {"errors": e.errors})
    else:
        plan_store.save_run_snapshot(
            run_id, runtime_plan.get("plan_id"), serialize_for_snapshot(runtime_plan)
        )

        systems = runtime_plan.get("systems", [])
        logger.info(
            "Plan '%s' – %d system, tổng %d step",
            runtime_plan.get("plan_name"),
            len(systems),
            sum(len(s.get("steps", [])) for s in systems),
        )

        if systems:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(systems))) as executor:
                futures = {
                    executor.submit(run_system_plan, sp): sp["system_name"]
                    for sp in systems
                }
                for future in as_completed(futures):
                    sys_name = futures[future]
                    try:
                        results = future.result()
                        all_results.extend(results)
                        logger.info(
                            "System [%s] hoàn thành – %d checks", sys_name, len(results)
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.exception("System [%s] lỗi nghiêm trọng", sys_name)
                        all_results.append(
                            CheckResult(sys_name, "-", "Runner", "ERROR", str(e))
                        )

    # Lưu kết quả vào DB
    for r in all_results:
        store.save_result(run_id, r)

    # Tổng hợp
    finished_at = datetime.now().isoformat()
    ok_count = sum(1 for r in all_results if r.status == "OK")
    warning_count = sum(1 for r in all_results if r.status == "WARNING")
    critical_count = sum(1 for r in all_results if r.status == "CRITICAL")
    error_count = sum(1 for r in all_results if r.status == "ERROR")

    summary.finished_at = finished_at
    summary.overall_status = _overall_status(all_results)
    summary.total_checks = len(all_results)
    summary.ok_count = ok_count
    summary.warning_count = warning_count
    summary.critical_count = critical_count
    summary.error_count = error_count
    summary.results = all_results
    store.save_run(summary)

    # Gửi báo cáo HTML
    html = generate_html_report(summary, all_results)
    subject = generate_subject(summary)
    alert_mgr.send_report(subject, html)

    # Lưu báo cáo HTML vào file
    _save_html_report(run_id, html)

    elapsed = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).seconds
    logger.info(
        f"=== Daily Check hoàn thành – {elapsed}s | "
        f"OK:{ok_count} WARN:{warning_count} CRIT:{critical_count} ERR:{error_count} "
        f"| Status: {summary.overall_status} ==="
    )
    return summary


def _save_html_report(run_id: str, html: str):
    """Lưu báo cáo HTML ra file để xem lại."""
    from pathlib import Path
    reports_dir = Path(get("general.data_dir", "data")) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"report_{run_id}.html"
    report_path.write_text(html, encoding="utf-8")
    logger.info(f"Báo cáo HTML lưu tại: {report_path}")
