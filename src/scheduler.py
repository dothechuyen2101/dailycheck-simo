"""
Scheduler – APScheduler chạy Daily Check tự động theo giờ cấu hình mỗi ngày.
Hỗ trợ trigger thủ công qua API.
"""
import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config_manager import get
from src.dynamic_modules import DynamicModuleStore
from src.orchestrator import run_daily_check, run_dynamic_module

logger = logging.getLogger("dailycheck.scheduler")

_scheduler: BackgroundScheduler | None = None
_last_run_lock = threading.Lock()
_is_running = False
_module_run_locks: dict[int, threading.Lock] = {}
_running_modules: set[int] = set()


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


def _dynamic_job_wrapper(module_id: int):
    lock = _module_run_locks.setdefault(module_id, threading.Lock())
    with lock:
        if module_id in _running_modules:
            logger.warning("Dynamic module %s đang chạy, bỏ qua lần trigger này", module_id)
            return
        _running_modules.add(module_id)
    try:
        run_dynamic_module(module_id)
    except Exception as e:
        logger.exception("Dynamic module %s gặp lỗi không xử lý được: %s", module_id, e)
    finally:
        with lock:
            _running_modules.discard(module_id)


def _schedule_dynamic_modules():
    if not _scheduler:
        return
    store = DynamicModuleStore()
    active_job_ids = {"daily_check"}
    for module in store.list_enabled_modules():
        hour, minute = module.daily_time.split(":")
        job_id = f"dynamic_module_{module.id}"
        trigger = CronTrigger(
            hour=int(hour),
            minute=int(minute),
            timezone=module.timezone,
        )
        _scheduler.add_job(
            _dynamic_job_wrapper,
            trigger=trigger,
            id=job_id,
            name=f"Dynamic Module {module.name}",
            replace_existing=True,
            misfire_grace_time=300,
            kwargs={"module_id": module.id},
        )
        active_job_ids.add(job_id)
    for job in list(_scheduler.get_jobs()):
        if job.id.startswith("dynamic_module_") and job.id not in active_job_ids:
            _scheduler.remove_job(job.id)


def start_scheduler():
    """Khởi động scheduler chạy nền."""
    global _scheduler

    check_time = get("general.check_time", "08:00")
    hour, minute = check_time.split(":")
    timezone = get("general.timezone", "Asia/Ho_Chi_Minh")

    _scheduler = BackgroundScheduler(timezone=timezone)

    # Chạy vào giờ cấu hình mỗi ngày
    trigger = CronTrigger(
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
    _schedule_dynamic_modules()
    _scheduler.start()
    logger.info(f"Scheduler đã khởi động – chạy {check_time} mỗi ngày ({timezone})")


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


def trigger_dynamic_module_now(module_id: int) -> str:
    t = threading.Thread(
        target=_dynamic_job_wrapper,
        kwargs={"module_id": module_id},
        name=f"DynamicModule-{module_id}",
        daemon=True,
    )
    t.start()
    return f"Dynamic module {module_id} được trigger lúc {datetime.now().isoformat()}"


def is_running() -> bool:
    return _is_running


def is_module_running(module_id: int) -> bool:
    return module_id in _running_modules


def resync_dynamic_jobs():
    if _scheduler and _scheduler.running:
        _schedule_dynamic_modules()


def get_dynamic_job(module_id: int):
    if not _scheduler:
        return None
    return _scheduler.get_job(f"dynamic_module_{module_id}")
