"""
Application Check Module – Kiểm tra WSO2, HAProxy, Kafka, Application Servers.
3 bước/ứng dụng: date/hostname, disk, service status (+ docker nếu có).
"""
import logging
import re
from typing import List, Optional

from src.builtin_commands import BuiltinCommandSet, BuiltinCommandStore, step_name
from src.checks.common import disk_space_check, service_check, system_info_check
from src.config_manager import get
from src.logger import CheckResult
from src.ssh_manager import ssh_connect

logger = logging.getLogger("dailycheck.app")


def _make(system, host, name, status, msg, raw="", details=None):
    return CheckResult(system, host, name, status, msg, raw, details or {})


def _ok(system, host, name, msg, raw="", details=None):
    return _make(system, host, name, "OK", msg, raw, details)

def _warn(system, host, name, msg, raw="", details=None):
    return _make(system, host, name, "WARNING", msg, raw, details)

def _crit(system, host, name, msg, raw="", details=None):
    return _make(system, host, name, "CRITICAL", msg, raw, details)

def _err(system, host, name, msg):
    return _make(system, host, name, "ERROR", msg)


def check_system_info(client, system: str, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "application.system_info"
    return _make(system, host_name, step_name(key), *system_info_check(client, commands, key, ctx))


def check_disk_space(client, system: str, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "application.disk_space"
    return _make(system, host_name, step_name(key), *disk_space_check(client, commands, key, ctx))


def check_service(client, system: str, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "application.service"
    name = f"{step_name(key)} {ctx['service_name']}"
    return _make(system, host_name, name, *service_check(client, commands, key, ctx))


def _split_two_columns(line: str) -> List[str]:
    """Tách 'TÊN<tab>GIÁ TRỊ' (hoặc cách bằng khoảng trắng) thành 2 phần."""
    return line.split("\t", 1) if "\t" in line else line.split(None, 1)


def check_docker_containers(client, system: str, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    """Kiểm tra Docker containers – tất cả phải running."""
    key = "application.docker"
    name = step_name(key)
    res = commands.run(client, key, "ps", ctx)
    if not res.success:
        return _err(system, host_name, name, f"docker ps thất bại: {res.stderr[:200]}")
    output = res.stdout
    not_running = []
    for line in output.splitlines():
        parts = _split_two_columns(line)
        if len(parts) < 2:
            continue
        cname, cstatus = parts[0], parts[1]
        if "up" not in cstatus.lower():
            not_running.append(f"{cname} ({cstatus})")

    # Kiểm tra tổng số replica qua docker service ls nếu có Swarm
    res_svc = commands.run(client, key, "service_ls", ctx)
    svc_issues = []
    if res_svc.success:
        for line in res_svc.stdout.splitlines():
            parts = _split_two_columns(line)
            if len(parts) < 2:
                continue
            svc_name, replicas = parts[0], parts[1]
            # Replica format: "2/2" – nếu không đủ thì cảnh báo
            m = re.match(r"(\d+)/(\d+)", replicas)
            if m and m.group(1) != m.group(2):
                svc_issues.append(f"{svc_name}: {replicas}")

    issues = not_running + svc_issues
    if issues:
        return _warn(system, host_name, name,
                     f"Docker issues: {', '.join(issues)}",
                     raw=output, details={"issues": issues})
    return _ok(system, host_name, name,
               "Tất cả Docker container đang chạy", raw=output)


def run_all(commands: Optional[BuiltinCommandSet] = None) -> List[CheckResult]:
    """Chạy kiểm tra tất cả ứng dụng được cấu hình."""
    if commands is None:
        commands = BuiltinCommandStore().load_command_set()
    apps = get("applications", [])
    results: List[CheckResult] = []

    for app in apps:
        app_name = app.get("name", "Unknown")
        service = app.get("service", "")
        has_docker = app.get("has_docker", False)
        hosts = app.get("hosts", [])

        steps = [
            ("application.system_info", check_system_info),
            ("application.disk_space", check_disk_space),
        ]
        if service:
            steps.append(("application.service", check_service))
        if has_docker:
            steps.append(("application.docker", check_docker_containers))
        steps = [fn for key, fn in steps if commands.is_enabled(key)]
        if not steps:
            continue

        for host_cfg in hosts:
            host_name = host_cfg.get("name", host_cfg["ip"])
            ctx = {
                "host_name": host_name,
                "host_ip": host_cfg["ip"],
                "app_name": app_name,
                "service_name": service,
            }
            logger.info(f"App [{app_name}]: kết nối {host_name}")
            batch = []
            try:
                with ssh_connect(host_cfg) as client:
                    for fn in steps:
                        batch.append(fn(client, app_name, host_name, commands, ctx))
            except ConnectionError as e:
                batch.append(_err(app_name, host_name, "Kết nối SSH", str(e)))
            except Exception as e:
                logger.exception(f"App [{app_name}]: lỗi trên {host_name}")
                batch.append(_err(app_name, host_name, "Lỗi không xác định", str(e)))

            results.extend(batch)

    return results
