"""
Scheduler – APScheduler chạy Daily Check tự động lúc 08:00 ngày làm việc.
Hỗ trợ trigger thủ công qua API.
"""
import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config_manager import get
from src.orchestrator import run_daily_check

logger = logging.getLogger("dailycheck.scheduler")

_scheduler: BackgroundScheduler | None = None
_last_run_lock = threading.Lock()
_is_running = False


def _job_wrapper():
    global _is_running
    with _last_run_lock:
        if _is_running:
            logger.warning("Daily Check đang chạy, bỏ qua lần trigger này")
            return
        _is_running = True
    try:
        run_daily_check()
    except Exception as e:
        logger.exception(f"Daily Check gặp lỗi không xử lý được: {e}")
    finally:
        with _last_run_lock:
            _is_running = False


def start_scheduler():
    """Khởi động scheduler chạy nền."""
    global _scheduler

    check_time = get("general.check_time", "08:00")
    hour, minute = check_time.split(":")
    timezone = get("general.timezone", "Asia/Ho_Chi_Minh")

    _scheduler = BackgroundScheduler(timezone=timezone)

    # Chạy vào giờ cấu hình, thứ 2 – thứ 6
    trigger = CronTrigger(
        day_of_week="mon-fri",
        hour=int(hour),
        minute=int(minute),
        timezone=timezone,
    )
    _scheduler.add_job(
        _job_wrapper,
        trigger=trigger,
        id="daily_check",
        name="SIMO Daily Check",
        replace_existing=True,
        misfire_grace_time=300,  # Cho phép trễ tối đa 5 phút
    )
    _scheduler.start()
    logger.info(f"Scheduler đã khởi động – chạy {check_time} mỗi ngày làm việc ({timezone})")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler đã dừng")


def trigger_now() -> str:
    """Trigger chạy Daily Check ngay lập tức (dùng cho manual trigger hoặc test)."""
    t = threading.Thread(target=_job_wrapper, name="ManualDailyCheck", daemon=True)
    t.start()
    return f"Daily Check được trigger lúc {datetime.now().isoformat()}"


def is_running() -> bool:
    return _is_running
