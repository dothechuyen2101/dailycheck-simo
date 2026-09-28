"""
Helper dùng chung cho các module check built-in.
Các hàm *_check trả về (status, message, raw_output, details) để mỗi module tự tạo CheckResult.
"""
import re
from typing import List, Tuple

from src.builtin_commands import BuiltinCommandSet
from src.config_manager import get

SQL_ERROR_RE = re.compile(r"(?:ORA|SP2)-\d+[^\n]*")
SKIPPED_MOUNTS = ("/cdrom", "/cdroom")

CheckOutcome = Tuple[str, str, str, dict]


def parse_df_issues(output: str, threshold: float) -> List[str]:
    """
    Trả về các phân vùng có Use% >= threshold.
    Đọc 2 cột cuối (Use% và Mounted on) nên đúng với df -k, -h, -T, -P
    và cả dòng bị ngắt khi tên device quá dài.
    """
    issues = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[-2].endswith("%"):
            continue
        mount = parts[-1]
        if mount.lower() in SKIPPED_MOUNTS:
            continue
        try:
            pct = int(parts[-2].rstrip("%"))
        except ValueError:
            continue
        if pct >= threshold:
            issues.append(f"{mount} ({pct}%)")
    return issues


def sql_errors(output: str) -> List[str]:
    """Các dòng lỗi ORA-/SP2- trong output sqlplus."""
    return SQL_ERROR_RE.findall(output)


def system_info_check(client, commands: BuiltinCommandSet, step_key: str, ctx: dict) -> CheckOutcome:
    res_date = commands.run(client, step_key, "date", ctx)
    res_host = commands.run(client, step_key, "hostname", ctx)
    failed = next((r for r in (res_date, res_host) if not r.success), None)
    if failed:
        return "ERROR", f"Không lấy được date/hostname: {failed.stderr[:200]}", "", {}
    return (
        "OK",
        f"date={res_date.stdout.strip()} | hostname={res_host.stdout.strip()}",
        f"date: {res_date.stdout}\nhostname: {res_host.stdout}",
        {},
    )


def disk_space_check(client, commands: BuiltinCommandSet, step_key: str, ctx: dict) -> CheckOutcome:
    threshold = get("thresholds.disk_usage_max_pct", 90)
    res = commands.run(client, step_key, "main", ctx)
    if not res.success:
        return "ERROR", f"Lệnh {res.command} thất bại: {res.stderr[:200]}", "", {}
    issues = parse_df_issues(res.stdout, threshold)
    if issues:
        return (
            "CRITICAL",
            f"Phân vùng vượt {threshold}%: {', '.join(issues)}",
            res.stdout,
            {"overloaded": issues},
        )
    return "OK", f"Tất cả phân vùng < {threshold}%", res.stdout, {}


def service_check(client, commands: BuiltinCommandSet, step_key: str, ctx: dict) -> CheckOutcome:
    service_name = ctx.get("service_name", "")
    res = commands.run(client, step_key, "main", ctx)
    if res.exit_code < 0:
        # Lệnh không chạy được (lệnh không hợp lệ, mất kết nối, timeout)
        return "ERROR", f"Không chạy được lệnh {res.command}: {res.stderr[:200]}", "", {}
    output = res.stdout + res.stderr
    if "active (running)" in output.lower():
        return "OK", f"Service {service_name} ACTIVE (running)", output, {}
    if "inactive" in output.lower() or "failed" in output.lower():
        return "CRITICAL", f"Service {service_name} không chạy", output, {}
    return "WARNING", f"Service {service_name} trạng thái không xác định", output, {}
