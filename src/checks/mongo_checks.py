"""
MongoDB Check Module – 3 bước kiểm tra MongoDB Cluster.
"""
import logging
from typing import List, Optional

from src.builtin_commands import BuiltinCommandSet, BuiltinCommandStore, step_name
from src.checks.common import disk_space_check, service_check, system_info_check
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


def check_system_info(client, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "mongodb.system_info"
    return CheckResult(SYSTEM, host_name, step_name(key), *system_info_check(client, commands, key, ctx))


def check_disk_space(client, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "mongodb.disk_space"
    return CheckResult(SYSTEM, host_name, step_name(key), *disk_space_check(client, commands, key, ctx))


def check_mongod_service(client, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "mongodb.service"
    return CheckResult(SYSTEM, host_name, step_name(key), *service_check(client, commands, key, ctx))


STEPS = [
    ("mongodb.system_info", check_system_info),
    ("mongodb.disk_space", check_disk_space),
    ("mongodb.service", check_mongod_service),
]


def run_all(commands: Optional[BuiltinCommandSet] = None) -> List[CheckResult]:
    """Chạy 3 bước kiểm tra MongoDB trên cả 2 node."""
    if commands is None:
        commands = BuiltinCommandStore().load_command_set()
    cfg = get("mongodb", {})
    hosts = cfg.get("hosts", [])
    service_name = cfg.get("service_name", "mongod")
    results: List[CheckResult] = []

    steps = [fn for key, fn in STEPS if commands.is_enabled(key)]
    if not steps:
        return results

    for host_cfg in hosts:
        host_name = host_cfg.get("name", host_cfg["ip"])
        ctx = {"host_name": host_name, "host_ip": host_cfg["ip"], "service_name": service_name}
        logger.info(f"MongoDB: kết nối {host_name}")
        try:
            with ssh_connect(host_cfg) as client:
                for fn in steps:
                    results.append(fn(client, host_name, commands, ctx))
        except ConnectionError as e:
            results.append(_err(host_name, "Kết nối SSH", str(e)))
        except Exception as e:
            logger.exception(f"MongoDB: lỗi trên {host_name}")
            results.append(_err(host_name, "Lỗi không xác định", str(e)))

    return results
