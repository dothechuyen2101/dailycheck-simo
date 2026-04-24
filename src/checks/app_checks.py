"""
Application Check Module – Kiểm tra WSO2, HAProxy, Kafka, Application Servers.
3 bước/ứng dụng: date/hostname, disk, service status (+ docker nếu có).
"""
import logging
import re
from typing import List

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


def check_system_info(client, system: str, host_name: str) -> CheckResult:
    name = "Bước 1 – Thông số hệ thống"
    r_date = client.run("date")
    r_host = client.run("hostname")
    if not r_date.success or not r_host.success:
        return _err(system, host_name, name, "Không lấy được date/hostname")
    return _ok(system, host_name, name,
               f"date={r_date.stdout.strip()} | hostname={r_host.stdout.strip()}",
               raw=f"date: {r_date.stdout}\nhostname: {r_host.stdout}")


def check_disk_space(client, system: str, host_name: str) -> CheckResult:
    name = "Bước 2 – Phân vùng đĩa"
    threshold = get("thresholds.disk_usage_max_pct", 90)
    res = client.run("df -kh")
    if not res.success:
        return _err(system, host_name, name, f"df -kh thất bại: {res.stderr}")
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
        return _crit(system, host_name, name,
                     f"Phân vùng vượt {threshold}%: {', '.join(issues)}",
                     raw=res.stdout, details={"overloaded": issues})
    return _ok(system, host_name, name,
               f"Tất cả phân vùng < {threshold}%", raw=res.stdout)


def check_service(client, system: str, host_name: str, service_name: str) -> CheckResult:
    name = f"Bước 3 – Service {service_name}"
    res = client.run(f"systemctl status {service_name}")
    output = res.stdout + res.stderr
    if "active (running)" in output.lower():
        return _ok(system, host_name, name,
                   f"Service {service_name} ACTIVE (running)", raw=output)
    if "inactive" in output.lower() or "failed" in output.lower():
        return _crit(system, host_name, name,
                     f"Service {service_name} không chạy",
                     raw=output)
    return _warn(system, host_name, name,
                 f"Service {service_name} trạng thái không xác định",
                 raw=output)


def check_docker_containers(client, system: str, host_name: str) -> CheckResult:
    """Kiểm tra Docker containers – tất cả phải running."""
    name = "Bước 3b – Docker Containers"
    res = client.run("docker ps --format '{{.Names}}\t{{.Status}}'")
    if not res.success:
        return _err(system, host_name, name, f"docker ps thất bại: {res.stderr[:200]}")
    output = res.stdout
    not_running = []
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        cname, cstatus = parts[0], parts[1]
        if "up" not in cstatus.lower():
            not_running.append(f"{cname} ({cstatus})")

    # Kiểm tra tổng số replica qua docker service ls nếu có Swarm
    res_svc = client.run("docker service ls --format '{{.Name}}\t{{.Replicas}}'")
    svc_issues = []
    if res_svc.success:
        for line in res_svc.stdout.splitlines():
            parts = line.split("\t", 1)
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


def run_all(alert_manager=None) -> List[CheckResult]:
    """Chạy kiểm tra tất cả ứng dụng được cấu hình."""
    apps = get("applications", [])
    results: List[CheckResult] = []

    for app in apps:
        app_name = app.get("name", "Unknown")
        service = app.get("service", "")
        has_docker = app.get("has_docker", False)
        hosts = app.get("hosts", [])

        for host_cfg in hosts:
            host_name = host_cfg.get("name", host_cfg["ip"])
            logger.info(f"App [{app_name}]: kết nối {host_name}")
            batch = []
            try:
                with ssh_connect(host_cfg) as client:
                    batch.append(check_system_info(client, app_name, host_name))
                    batch.append(check_disk_space(client, app_name, host_name))
                    if service:
                        batch.append(check_service(client, app_name, host_name, service))
                    if has_docker:
                        batch.append(check_docker_containers(client, app_name, host_name))
            except ConnectionError as e:
                batch.append(_err(app_name, host_name, "Kết nối SSH", str(e)))
            except Exception as e:
                logger.exception(f"App [{app_name}]: lỗi trên {host_name}")
                batch.append(_err(app_name, host_name, "Lỗi không xác định", str(e)))

            results.extend(batch)

    return results
