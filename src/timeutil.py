"""
Giờ hiện tại theo general.timezone trong config, không phụ thuộc múi giờ của máy chủ/container
(container python:slim thiếu tzdata hoặc server đặt UTC thì datetime.now() trả về giờ UTC).
"""
import logging
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config_manager import get

logger = logging.getLogger("dailycheck.timeutil")

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
# Dùng khi máy không có dữ liệu múi giờ (image cũ thiếu tzdata); các múi giờ này không có giờ mùa hè
_FIXED_OFFSET_HOURS = {"Asia/Ho_Chi_Minh": 7, "Asia/Saigon": 7, "Asia/Bangkok": 7, "Asia/Jakarta": 7, "UTC": 0}


@lru_cache(maxsize=8)
def _zone(name: str) -> Optional[tzinfo]:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name in _FIXED_OFFSET_HOURS:
            return timezone(timedelta(hours=_FIXED_OFFSET_HOURS[name]), name)
        logger.warning("Không tìm thấy múi giờ %s (thiếu tzdata?), dùng giờ của máy chủ", name)
        return None


def now() -> datetime:
    """Giờ hiện tại theo general.timezone, dạng naive để giữ định dạng isoformat đã lưu trước đây."""
    try:
        name = get("general.timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    except OSError:
        # Chưa có config.yaml (vd. khi chạy test) – dùng múi giờ mặc định
        name = DEFAULT_TIMEZONE
    tz = _zone(name)
    if tz is None:
        return datetime.now()
    return datetime.now(tz).replace(tzinfo=None)
