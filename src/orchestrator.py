"""
Orchestrator – Điều phối chạy song song tất cả check modules,
tổng hợp kết quả và tính toán overall status.
"""
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List

from src.config_manager import get
from src.logger import CheckResult, RunSummary, ResultStore
from src.alert_manager import AlertManager
from src.report_generator import generate_html_report, generate_subject
import src.checks.oracle_checks as oracle_checks
import src.checks.mongo_checks as mongo_checks
import src.checks.hadoop_checks as hadoop_checks
import src.checks.app_checks as app_checks

logger = logging.getLogger("dailycheck.orchestrator")


def _overall_status(results: List[CheckResult]) -> str:
    statuses = {r.status for r in results}
    if "CRITICAL" in statuses or "ERROR" in statuses:
        return "CRITICAL"
    if "WARNING" in statuses:
        return "WARNING"
    return "OK"


def run_daily_check() -> RunSummary:
    """
    Entry point chính – chạy toàn bộ Daily Check.
    Trả về RunSummary với đầy đủ kết quả.
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    started_at = datetime.now().isoformat()
    logger.info(f"=== SIMO Daily Check bắt đầu – Run ID: {run_id} ===")

    store = ResultStore()
    alert_mgr = AlertManager()
    max_workers = get("general.max_workers", 10)

    # Đăng ký run vào DB ngay
    summary = RunSummary(run_id=run_id, started_at=started_at)
    store.save_run(summary)

    all_results: List[CheckResult] = []

    # Chạy song song các module
    modules = [
        ("Oracle", oracle_checks.run_all),
        ("MongoDB", mongo_checks.run_all),
        ("Hadoop", hadoop_checks.run_all),
        ("Application", app_checks.run_all),
    ]

    with ThreadPoolExecutor(max_workers=min(max_workers, len(modules))) as executor:
        futures = {
            executor.submit(fn, alert_mgr): name
            for name, fn in modules
        }
        for future in as_completed(futures):
            module_name = futures[future]
            try:
                results = future.result()
                all_results.extend(results)
                logger.info(f"Module [{module_name}] hoàn thành – {len(results)} checks")
            except Exception as e:
                logger.exception(f"Module [{module_name}] gặp lỗi nghiêm trọng: {e}")

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
