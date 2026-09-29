"""
Application Check Module – Kiểm tra WSO2, HAProxy, Kafka, Application Servers.
3 bước/ứng dụng: date/hostname, disk, service status (+ docker nếu có).
"""
import logging
import re
from typing import List, Optional

from src.builtin_commands import BuiltinCommandSet, BuiltinCommandStore, step_name
from src.checks.common import disk_space_check, process_check, service_check, system_info_check
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


def check_process_service(client, system: str, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "application.process"
    name = f"{step_name(key)} {ctx['service_name']}"
    return _make(system, host_name, name, *process_check(client, commands, key, ctx))


def _split_two_columns(line: str) -> List[str]:
    """Tách 'TÊN<tab>GIÁ TRỊ' (hoặc cách bằng khoảng trắng) thành 2 phần."""
    return line.split("\t", 1) if "\t" in line else line.split(None, 1)


def check_docker_service(client, system: str, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    """Kiểm tra service chạy bằng Docker Swarm (service_type: docker) thay cho systemctl."""
    key = "application.docker_service"
    service_name = ctx["service_name"]
    name = f"{step_name(key)} {service_name}"

    # Trên Swarm manager: so số replica đang chạy với số replica yêu cầu
    res_svc = commands.run(client, key, "service_ls", ctx)
    rows = []
    if res_svc.success:
        rows = [parts for parts in map(_split_two_columns, res_svc.stdout.splitlines()) if len(parts) == 2]
    # --filter name= khớp theo tiền tố, ưu tiên dòng trùng tên chính xác
    rows = [r for r in rows if r[0] == service_name] or rows
    if rows:
        not_ready, down = [], []
        for svc, replicas in rows:
            m = re.match(r"(\d+)/(\d+)", replicas)
            if m and int(m.group(1)) == 0 and int(m.group(2)) > 0:
                down.append(f"{svc}: {replicas}")
            elif m and m.group(1) != m.group(2):
                not_ready.append(f"{svc}: {replicas}")
        summary = ", ".join(f"{svc}: {replicas}" for svc, replicas in rows)
        if down:
            return _crit(system, host_name, name, f"Service không có replica nào chạy: {', '.join(down)}",
                         raw=res_svc.stdout, details={"issues": down + not_ready})
        if not_ready:
            return _warn(system, host_name, name, f"Service thiếu replica: {', '.join(not_ready)}",
                         raw=res_svc.stdout, details={"issues": not_ready})
        return _ok(system, host_name, name, f"Service đủ replica ({summary})", raw=res_svc.stdout)

    # Không phải manager (hoặc không có Swarm service trùng tên): xem container trên node này
    res_ps = commands.run(client, key, "ps", ctx)
    if not res_ps.success:
        return _err(system, host_name, name, f"docker ps thất bại: {res_ps.stderr[:200]}")
    running = [
        parts[0] for parts in map(_split_two_columns, res_ps.stdout.splitlines())
        if len(parts) == 2 and "up" in parts[1].lower()
    ]
    if running:
        return _ok(system, host_name, name,
                   f"{len(running)} container của {service_name} đang chạy trên node", raw=res_ps.stdout)
    if res_svc.success:
        return _crit(system, host_name, name,
                     f"Không tìm thấy Swarm service {service_name} và không có container nào đang chạy",
                     raw=res_svc.stdout)
    # Node worker: Swarm tự phân bổ replica nên node không chạy container nào cũng là bình thường;
    # số replica được kiểm tra trên node manager
    return _ok(system, host_name, name,
               f"Node worker không chạy container nào của {service_name} "
               f"(replica được kiểm tra trên Swarm manager)",
               raw=res_ps.stdout)


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


SERVICE_CHECKS = {
    "systemd": ("application.service", check_service),
    "docker": ("application.docker_service", check_docker_service),
    "process": ("application.process", check_process_service),
}


def run_all(commands: Optional[BuiltinCommandSet] = None) -> List[CheckResult]:
    """Chạy kiểm tra tất cả ứng dụng được cấu hình."""
    if commands is None:
        commands = BuiltinCommandStore().load_command_set()
    apps = get("applications", [])
    results: List[CheckResult] = []

    for app in apps:
        app_name = app.get("name", "Unknown")
        has_docker = app.get("has_docker", False)
        hosts = app.get("hosts", [])

        for host_cfg in hosts:
            host_name = host_cfg.get("name", host_cfg["ip"])
            # service / service_type khai báo ở host ghi đè giá trị của ứng dụng
            service = host_cfg.get("service", app.get("service", ""))
            service_type = str(host_cfg.get("service_type", app.get("service_type", "systemd"))).lower()
            if service_type not in SERVICE_CHECKS:
                logger.warning(f"App [{app_name}]: service_type '{service_type}' không hợp lệ, dùng systemd")
                service_type = "systemd"

            steps = [
                ("application.system_info", check_system_info),
                ("application.disk_space", check_disk_space),
            ]
            if service:
                steps.append(SERVICE_CHECKS[service_type])
            if has_docker:
                steps.append(("application.docker", check_docker_containers))
            steps = [fn for key, fn in steps if commands.is_enabled(key)]
            if not steps:
                continue

            ctx = {
                "host_name": host_name,
                "host_ip": host_cfg["ip"],
                "app_name": app_name,
                "service_name": service,
                # Tên tiến trình để kiểm tra khi unit systemd ở trạng thái active (exited)
                "process_pattern": host_cfg.get("process_pattern", app.get("process_pattern", "")),
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
