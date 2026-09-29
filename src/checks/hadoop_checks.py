"""
Hadoop Check Module – 3 bước kiểm tra Hadoop Cluster.
"""
import logging
import re
from typing import List, Optional

from src.builtin_commands import BuiltinCommandSet, BuiltinCommandStore, step_name
from src.checks.common import disk_space_check, system_info_check
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


def check_system_info(client, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "hadoop.system_info"
    return CheckResult(SYSTEM, host_name, step_name(key), *system_info_check(client, commands, key, ctx))


def check_disk_space(client, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "hadoop.disk_space"
    return CheckResult(SYSTEM, host_name, step_name(key), *disk_space_check(client, commands, key, ctx))


def check_hdfs_report(client, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "hadoop.hdfs_report"
    name = step_name(key)
    res = commands.run(client, key, "main", ctx)
    if not res.success and not res.stdout:
        return _err(host_name, name, f"{res.command} thất bại: {res.stderr[:200]}")
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


# Bước 1 & 2 chạy trên mọi node; HDFS report chỉ chạy trên namenode
ALL_NODE_STEPS = [
    ("hadoop.system_info", check_system_info),
    ("hadoop.disk_space", check_disk_space),
]
NAMENODE_STEPS = [
    ("hadoop.hdfs_report", check_hdfs_report),
]


def run_all(commands: Optional[BuiltinCommandSet] = None) -> List[CheckResult]:
    """Chạy 3 bước kiểm tra Hadoop trên tất cả node; HDFS report từ namenode."""
    if commands is None:
        commands = BuiltinCommandStore().load_command_set()
    cfg = get("hadoop", {})
    nodes = cfg.get("nodes", [])
    namenode_ip = cfg.get("namenode_ip", "")
    results: List[CheckResult] = []

    for node_cfg in nodes:
        host_name = node_cfg.get("name", node_cfg["ip"])
        is_namenode = node_cfg["ip"] == namenode_ip or namenode_ip == ""
        steps = ALL_NODE_STEPS + (NAMENODE_STEPS if is_namenode else [])
        steps = [fn for key, fn in steps if commands.is_enabled(key)]
        if not steps:
            continue
        ctx = {"host_name": host_name, "host_ip": node_cfg["ip"]}
        logger.info(f"Hadoop: kết nối {host_name}")
        try:
            with ssh_connect(node_cfg) as client:
                for fn in steps:
                    results.append(fn(client, host_name, commands, ctx))
        except ConnectionError as e:
            results.append(_err(host_name, "Kết nối SSH", str(e)))
        except Exception as e:
            logger.exception(f"Hadoop: lỗi trên {host_name}")
            results.append(_err(host_name, "Lỗi không xác định", str(e)))

    return results
