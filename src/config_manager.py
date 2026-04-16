"""
Config Manager – Tải và cung cấp cấu hình từ file YAML.
"""
import os
import yaml
from pathlib import Path


_config: dict = {}


def load_config(config_path: str | None = None) -> dict:
    """Tải config từ file YAML. Gọi một lần khi khởi động."""
    global _config
    if config_path is None:
        base = Path(__file__).parent.parent
        config_path = base / "config" / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)
    return _config


def get_config() -> dict:
    """Trả về config đã tải. Nếu chưa tải sẽ tải mặc định."""
    if not _config:
        load_config()
    return _config


def get(key: str, default=None):
    """Truy cập config theo dotted key (vd: 'general.ssh_timeout')."""
    cfg = get_config()
    keys = key.split(".")
    val = cfg
    for k in keys:
        if not isinstance(val, dict):
            return default
        val = val.get(k, default)
    return val
