"""
MongoDB Check Module – 3 bước kiểm tra MongoDB Cluster.
"""
import logging
from typing import List

from src.config_manager import get
from src.logger import CheckResult
from src.ssh_manager import ssh_connect

logger = logging.getLogger("dailycheck.mongodb")

SYSTEM = "MongoDB"


def _ok(host, name, msg, raw="", details=None):
    return CheckResult(SYSTEM, host, name, "OK", msg, raw, details or {})

def _warn(host, name, msg, raw="", details=None):
    return CheckResult(SYSTEM, host, name, "WARNING", msg, raw, details or {})

def _crit(host, name, msg, raw="", details=None):
    return CheckResult(SYSTEM, host, name, "CRITICAL", msg, raw, details or {})

def _err(host, name, msg):
    return CheckResult(SYSTEM, host, name, "ERROR", msg)


def check_system_info(client, host_name: str) -> CheckResult:
    name = "Bước 1 – Thông số hệ thống"
    r_date = client.run("date")
    r_host = client.run("hostname")
    if not r_date.success or not r_host.success:
        return _err(host_name, name, "Không lấy được date/hostname")
    return _ok(host_name, name,
               f"date={r_date.stdout.strip()} | hostname={r_host.stdout.strip()}",
               raw=f"date: {r_date.stdout}\nhostname: {r_host.stdout}")


def check_disk_space(client, host_name: str) -> CheckResult:
    name = "Bước 2 – Phân vùng đĩa"
    threshold = get("thresholds.disk_usage_max_pct", 90)
    res = client.run("df -kh")
    if not res.success:
        return _err(host_name, name, f"df -kh thất bại: {res.stderr}")
    issues = []
    for line in res.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        mount = parts[5]
        if mount.lower() in ("/cdrom", "/cdroom"):
            continue
        pct_str = parts[4].replace("%", "")
        try:
            pct = int(pct_str)
        except ValueError:
            continue
        if pct >= threshold:
            issues.append(f"{mount} ({pct}%)")
    if issues:
        return _crit(host_name, name,
                     f"Phân vùng vượt {threshold}%: {', '.join(issues)}",
                     raw=res.stdout, details={"overloaded": issues})
    return _ok(host_name, name, f"Tất cả phân vùng < {threshold}%", raw=res.stdout)


def check_mongod_service(client, host_name: str, service_name: str = "mongod") -> CheckResult:
    name = "Bước 3 – Trạng thái mongod"
    res = client.run(f"systemctl status {service_name}")
    output = res.stdout + res.stderr
    if "active (running)" in output.lower():
        return _ok(host_name, name, f"Service {service_name} ACTIVE (running)", raw=output)
    if "inactive" in output.lower() or "failed" in output.lower():
        return _crit(host_name, name,
                     f"Service {service_name} không chạy",
                     raw=output)
    return _warn(host_name, name,
                 f"Service {service_name} trạng thái không xác định",
                 raw=output)


def run_all(alert_manager=None) -> List[CheckResult]:
    """Chạy 3 bước kiểm tra MongoDB trên cả 2 node."""
    cfg = get("mongodb", {})
    hosts = cfg.get("hosts", [])
    service_name = cfg.get("service_name", "mongod")
    results: List[CheckResult] = []

    for host_cfg in hosts:
        host_name = host_cfg.get("name", host_cfg["ip"])
        logger.info(f"MongoDB: kết nối {host_name}")
        try:
            with ssh_connect(host_cfg) as client:
                results.append(check_system_info(client, host_name))
                results.append(check_disk_space(client, host_name))
                results.append(check_mongod_service(client, host_name, service_name))
        except ConnectionError as e:
            results.append(_err(host_name, "Kết nối SSH", str(e)))
        except Exception as e:
            logger.exception(f"MongoDB: lỗi trên {host_name}")
            results.append(_err(host_name, "Lỗi không xác định", str(e)))

    return results
