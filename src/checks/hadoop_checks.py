"""
Hadoop Check Module – 3 bước kiểm tra Hadoop Cluster.
"""
import logging
import re
from typing import List

from src.config_manager import get
from src.logger import CheckResult
from src.ssh_manager import ssh_connect

logger = logging.getLogger("dailycheck.hadoop")

SYSTEM = "Hadoop"


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


def check_hdfs_report(client, host_name: str) -> CheckResult:
    name = "Bước 3 – HDFS dfsadmin report"
    res = client.run("hdfs dfsadmin -report", timeout=60)
    output = res.stdout + res.stderr

    # Kiểm tra Safe Mode
    if re.search(r"safe mode is ON", output, re.IGNORECASE):
        return _crit(host_name, name,
                     "HDFS đang trong Safe Mode – cần kiểm tra ngay",
                     raw=output)

    # Đếm Live DataNodes
    live_match = re.search(r"Live datanodes\s*\((\d+)\)", output, re.IGNORECASE)
    dead_match = re.search(r"Dead datanodes\s*\((\d+)\)", output, re.IGNORECASE)
    live_count = int(live_match.group(1)) if live_match else 0
    dead_count = int(dead_match.group(1)) if dead_match else 0

    if dead_count > 0:
        return _warn(host_name, name,
                     f"Có {dead_count} DataNode DEAD (Live: {live_count})",
                     raw=output, details={"live": live_count, "dead": dead_count})
    if live_count == 0:
        return _crit(host_name, name,
                     "Không có Live DataNode",
                     raw=output)

    # Kiểm tra DFS Used%
    used_match = re.search(r"DFS Used%:\s*([\d.]+)%", output)
    used_pct = float(used_match.group(1)) if used_match else 0
    threshold = get("thresholds.disk_usage_max_pct", 90)
    if used_pct >= threshold:
        return _warn(host_name, name,
                     f"HDFS DFS Used = {used_pct}% (ngưỡng {threshold}%)",
                     raw=output, details={"dfs_used_pct": used_pct})

    return _ok(host_name, name,
               f"HDFS OK – Live DataNodes: {live_count}, DFS Used: {used_pct}%",
               raw=output, details={"live": live_count, "dfs_used_pct": used_pct})


def run_all(alert_manager=None) -> List[CheckResult]:
    """Chạy 3 bước kiểm tra Hadoop trên tất cả node; HDFS report từ namenode."""
    cfg = get("hadoop", {})
    nodes = cfg.get("nodes", [])
    namenode_ip = cfg.get("namenode_ip", "")
    results: List[CheckResult] = []

    for node_cfg in nodes:
        host_name = node_cfg.get("name", node_cfg["ip"])
        logger.info(f"Hadoop: kết nối {host_name}")
        try:
            with ssh_connect(node_cfg) as client:
                results.append(check_system_info(client, host_name))
                results.append(check_disk_space(client, host_name))
                # HDFS report chỉ chạy trên namenode
                if node_cfg["ip"] == namenode_ip or namenode_ip == "":
                    results.append(check_hdfs_report(client, host_name))
        except ConnectionError as e:
            results.append(_err(host_name, "Kết nối SSH", str(e)))
        except Exception as e:
            logger.exception(f"Hadoop: lỗi trên {host_name}")
            results.append(_err(host_name, "Lỗi không xác định", str(e)))

        if alert_manager:
            for r in results[-3:]:
                alert_manager.alert(r)

    return results
