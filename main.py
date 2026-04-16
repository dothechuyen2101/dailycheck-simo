"""
SIMO Automated Daily Check System – Entry Point.

Sử dụng:
  python main.py              # Chạy scheduler + dashboard (chế độ production)
  python main.py --run-now    # Chạy Daily Check ngay lập tức rồi thoát
  python main.py --dashboard  # Chỉ chạy dashboard (không có scheduler)
  python main.py --scheduler  # Chỉ chạy scheduler (không có dashboard)
"""
import argparse
import logging
import signal
import sys
import time

from src.config_manager import load_config
from src.logger import setup_logging


def parse_args():
    parser = argparse.ArgumentParser(
        description="SIMO Automated Daily Check System"
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Chạy Daily Check ngay lập tức rồi thoát"
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Chỉ chạy web dashboard (không scheduler)"
    )
    parser.add_argument(
        "--scheduler", action="store_true",
        help="Chỉ chạy scheduler (không dashboard)"
    )
    parser.add_argument(
        "--config", default=None,
        help="Đường dẫn file config YAML (mặc định: config/config.yaml)"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Mức độ log (mặc định: INFO)"
    )
    return parser.parse_args()


def run_all_now():
    """Chạy Daily Check một lần rồi thoát."""
    from src.orchestrator import run_daily_check
    logger = logging.getLogger("dailycheck")
    logger.info("Chạy Daily Check thủ công...")
    summary = run_daily_check()
    logger.info(f"Hoàn thành: {summary.overall_status} | "
                f"OK:{summary.ok_count} WARN:{summary.warning_count} "
                f"CRIT:{summary.critical_count} ERR:{summary.error_count}")
    return 0 if summary.overall_status == "OK" else 1


def run_with_scheduler_and_dashboard():
    """Chạy cả scheduler và dashboard cùng lúc."""
    from src import scheduler as sched
    from src.config_manager import get
    from dashboard.app import create_app
    import threading

    logger = logging.getLogger("dailycheck")

    # Khởi động scheduler
    sched.start_scheduler()

    # Chạy Flask trong thread riêng
    flask_app = create_app()
    host = get("dashboard.host", "0.0.0.0")
    port = get("dashboard.port", 5000)

    def run_flask():
        flask_app.run(host=host, port=port, debug=False, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Dashboard khởi động tại http://{host}:{port}")

    # Giữ process sống
    def _shutdown(sig, frame):
        logger.info("Nhận tín hiệu dừng – shutdown...")
        sched.stop_scheduler()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Hệ thống SIMO Daily Check đang chạy. Nhấn Ctrl+C để dừng.")
    while True:
        time.sleep(1)


def run_scheduler_only():
    """Chỉ chạy scheduler, không dashboard."""
    from src import scheduler as sched

    logger = logging.getLogger("dailycheck")
    sched.start_scheduler()

    def _shutdown(sig, frame):
        sched.stop_scheduler()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Scheduler đang chạy. Nhấn Ctrl+C để dừng.")
    while True:
        time.sleep(1)


def run_dashboard_only():
    """Chỉ chạy Flask dashboard."""
    from src.config_manager import get
    from dashboard.app import create_app

    flask_app = create_app()
    host = get("dashboard.host", "0.0.0.0")
    port = get("dashboard.port", 5000)
    debug = get("dashboard.debug", False)
    flask_app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    args = parse_args()
    load_config(args.config)
    setup_logging(args.log_level)

    if args.run_now:
        sys.exit(run_all_now())
    elif args.dashboard:
        run_dashboard_only()
    elif args.scheduler:
        run_scheduler_only()
    else:
        run_with_scheduler_and_dashboard()
