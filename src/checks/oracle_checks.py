"""
Oracle Check Module – 11 bước kiểm tra Oracle RAC theo kịch bản Daily Check SIMO.
"""
import logging
import re
from typing import List, Optional

from src.builtin_commands import BuiltinCommandSet, BuiltinCommandStore, step_name
from src.checks.common import disk_space_check, sql_errors, system_info_check
from src.config_manager import get
from src.logger import CheckResult
from src.ssh_manager import CommandResult, SSHClient, ssh_connect

logger = logging.getLogger("dailycheck.oracle")

SYSTEM = "Oracle"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(host, name, msg, raw="", details=None) -> CheckResult:
    return CheckResult(SYSTEM, host, name, "OK", msg, raw, details or {})

def _warn(host, name, msg, raw="", details=None) -> CheckResult:
    return CheckResult(SYSTEM, host, name, "WARNING", msg, raw, details or {})

def _crit(host, name, msg, raw="", details=None) -> CheckResult:
    return CheckResult(SYSTEM, host, name, "CRITICAL", msg, raw, details or {})

def _err(host, name, msg, raw="") -> CheckResult:
    return CheckResult(SYSTEM, host, name, "ERROR", msg, raw)


def _sql_failure(res: CommandResult) -> Optional[str]:
    """Mô tả lỗi nếu sqlplus chạy lỗi (có ORA-/SP2- hoặc exit code khác 0), ngược lại None."""
    errors = sql_errors(f"{res.stdout}\n{res.stderr}")
    if errors:
        return "; ".join(errors[:3])
    if not res.success:
        return f"exit code {res.exit_code}: {(res.stderr or res.stdout)[:200]}"
    return None


# ── Bước 1: Kiểm tra thông số hệ thống (date + hostname) ─────────────────────

def check_system_info(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.system_info"
    return CheckResult(SYSTEM, host_name, step_name(key), *system_info_check(client, commands, key, ctx))


# ── Bước 2: Kiểm tra phân vùng đĩa ───────────────────────────────────────────

def check_disk_space(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.disk_space"
    return CheckResult(SYSTEM, host_name, step_name(key), *disk_space_check(client, commands, key, ctx))


# ── Bước 3: Kiểm tra CRS (crs_stat -t) ───────────────────────────────────────

def _command_not_found(res: CommandResult) -> bool:
    stderr = res.stderr.lower()
    return res.exit_code == 127 or "not found" in stderr or "no such file" in stderr


def check_crs_status(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.crs_status"
    name = step_name(key)
    allowed_offline = get("oracle.crs_allowed_offline", [
        "ora.gsd", "ora.oc4j", "ora....bkup.db",
    ])
    res = commands.run(client, key, "main", ctx)
    if not res.success and _command_not_found(res) and commands.has_command(key, "fallback"):
        # Thử lệnh dự phòng (vd. đường dẫn đầy đủ trong grid home)
        res = commands.run(client, key, "fallback", ctx)
    if not res.success:
        return _err(host_name, name, f"{res.command} thất bại: {res.stderr[:200]}")

    issues = []
    for line in res.stdout.splitlines():
        if not line.strip() or line.startswith("Name") or line.startswith("---"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        svc_name = parts[0].lower()
        target = parts[2].upper() if len(parts) > 2 else ""
        state = parts[3].upper() if len(parts) > 3 else ""
        # Cho phép offline các service ngoại lệ
        if any(allowed.lower() in svc_name for allowed in allowed_offline):
            continue
        if target != state:
            issues.append(f"{parts[0]} (target={target}, state={state})")

    if issues:
        return _crit(host_name, name,
                     f"CRS không đồng bộ target/state: {', '.join(issues)}",
                     raw=res.stdout, details={"issues": issues})
    return _ok(host_name, name, "Tất cả CRS resource đồng bộ Target=State", raw=res.stdout)


# ── Bước 4: Kiểm tra OCR ────────────────────────────────────────────────────

def check_ocr(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.ocr"
    name = step_name(key)
    res = commands.run(client, key, "main", ctx)
    if not res.success and not res.stdout:
        return _err(host_name, name, f"{res.command} thất bại: {res.stderr[:200]}")
    output = res.stdout + res.stderr
    if "integrity check succeeded" not in output.lower():
        return _crit(host_name, name,
                     "OCR integrity check KHÔNG thành công",
                     raw=output)
    if "cluster registry integrity check succeeded" not in output.lower():
        return _warn(host_name, name,
                     "OCR device OK nhưng Cluster registry integrity check chưa xác nhận",
                     raw=output)
    return _ok(host_name, name,
               "OCR integrity check succeeded + Cluster registry OK",
               raw=output)


# ── Bước 5: Kiểm tra Voting Disk ─────────────────────────────────────────────

def check_voting_disk(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.voting_disk"
    name = step_name(key)
    required = get("thresholds.voting_disk_required", 3)
    res = commands.run(client, key, "main", ctx)
    if not res.success and not res.stdout:
        return _err(host_name, name, f"{res.command} thất bại: {res.stderr[:200]}")

    output = res.stdout + res.stderr
    # Đếm ONLINE voting disks
    online_count = len(re.findall(r"\bONLINE\b", output, re.IGNORECASE))
    # Kiểm tra tổng số disk từ dòng "Located N voting disk(s)"
    located_match = re.search(r"Located\s+(\d+)\s+voting disk", output, re.IGNORECASE)
    total_located = int(located_match.group(1)) if located_match else online_count

    if online_count < required or total_located < required:
        return _crit(host_name, name,
                     f"Chỉ có {online_count} ONLINE voting disk (yêu cầu {required})",
                     raw=output, details={"online": online_count, "required": required})
    return _ok(host_name, name,
               f"{online_count}/{total_located} voting disk ONLINE",
               raw=output)


# ── Bước 6: Kiểm tra Archive Switch ─────────────────────────────────────────

def check_archive_switch(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.archive_switch"
    name = step_name(key)
    res = commands.run(client, key, "main", ctx)
    output = f"{res.stdout}\n{res.stderr}".strip()
    if "System altered" in output:
        return _ok(host_name, name, "Archive switch thành công: System altered", raw=output)
    if "ORA-" in output:
        ora_errors = re.findall(r"ORA-\d+[^\n]*", output)
        return _crit(host_name, name,
                     f"Archive switch thất bại: {'; '.join(ora_errors[:3])}",
                     raw=output)
    failure = _sql_failure(res)
    if failure:
        return _err(host_name, name, f"sqlplus thất bại: {failure}", raw=output)
    return _warn(host_name, name,
                 "Kết quả archive switch không xác định, kiểm tra thủ công",
                 raw=output)


# ── Bước 7: Kiểm tra Free Tablespace ────────────────────────────────────────

def check_free_tablespace(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.free_tablespace"
    name = step_name(key)
    thresholds = get("thresholds.tablespace", {})
    sys_min_pct = thresholds.get("system_min_free_pct", 3)
    undo_min_mb = thresholds.get("undotbs_min_total_mb", 400)
    default_min_pct = thresholds.get("default_min_free_pct", 15)

    res = commands.run(client, key, "main", ctx)
    output = res.stdout
    failure = _sql_failure(res)
    if failure:
        return _err(host_name, name, f"Truy vấn tablespace thất bại: {failure}",
                    raw=f"{res.stdout}\n{res.stderr}".strip())

    issues = []
    for line in output.splitlines():
        parts = line.split()
        # Mỗi dòng dữ liệu có dạng: NAME  TOTAL  USED  FREE  PCT_FREE
        if len(parts) < 5:
            continue
        ts_name = parts[0].upper()
        try:
            total_mb = float(parts[1].replace(",", ""))
            pct_free = float(parts[4])
        except ValueError:
            continue

        if ts_name in ("SYSTEM", "SYSAUX", "EXAMPLE"):
            if pct_free < sys_min_pct:
                issues.append(f"{ts_name}: %Free={pct_free:.1f}% < {sys_min_pct}%")
        elif ts_name.startswith("UNDOTBS"):
            if total_mb < undo_min_mb:
                issues.append(f"{ts_name}: Total={total_mb:.0f}MB < {undo_min_mb}MB")
        else:
            if pct_free < default_min_pct:
                issues.append(f"{ts_name}: %Free={pct_free:.1f}% < {default_min_pct}%")

    if issues:
        return _crit(host_name, name,
                     f"Tablespace vượt ngưỡng: {'; '.join(issues)}",
                     raw=output, details={"issues": issues})
    return _ok(host_name, name, "Tất cả tablespace đều đạt ngưỡng", raw=output)


# ── Bước 8: Kiểm tra Unusable Indexes ───────────────────────────────────────

def check_unusable_indexes(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.unusable_indexes"
    name = step_name(key)
    res = commands.run(client, key, "main", ctx)
    output = res.stdout
    failure = _sql_failure(res)
    if failure:
        return _err(host_name, name, f"Truy vấn unusable index thất bại: {failure}",
                    raw=f"{res.stdout}\n{res.stderr}".strip())
    if "no rows selected" in output.lower():
        return _ok(host_name, name, "Không có unusable index – no rows selected", raw=output)
    # Đếm số index cần rebuild
    rebuild_stmts = [l for l in output.splitlines() if "alter index" in l.lower()]
    if rebuild_stmts:
        return _warn(host_name, name,
                     f"Phát hiện {len(rebuild_stmts)} unusable index cần rebuild",
                     raw=output, details={"count": len(rebuild_stmts)})
    return _ok(host_name, name, "Không có unusable index", raw=output)


# ── Bước 9: Kiểm tra Invalid Objects ────────────────────────────────────────

def check_invalid_objects(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.invalid_objects"
    name = step_name(key)
    res = commands.run(client, key, "main", ctx)
    output = res.stdout
    failure = _sql_failure(res)
    if failure:
        return _err(host_name, name, f"Truy vấn invalid object thất bại: {failure}",
                    raw=f"{res.stdout}\n{res.stderr}".strip())
    if "no rows selected" in output.lower():
        return _ok(host_name, name, "Không có invalid object – no rows selected", raw=output)
    compile_stmts = [l for l in output.splitlines() if "compile" in l.lower()]
    if compile_stmts:
        return _warn(host_name, name,
                     f"Phát hiện {len(compile_stmts)} invalid object cần compile",
                     raw=output, details={"count": len(compile_stmts)})
    return _ok(host_name, name, "Không có invalid object", raw=output)


# ── Bước 10: Kiểm tra ASM Disk Group ────────────────────────────────────────

def check_asm_diskgroup(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.asm_diskgroup"
    name = step_name(key)
    threshold = get("thresholds.asm_usage_max_pct", 90)
    res = commands.run(client, key, "main", ctx)
    output = res.stdout
    failure = _sql_failure(res)
    if failure:
        return _err(host_name, name, f"Truy vấn ASM diskgroup thất bại: {failure}",
                    raw=f"{res.stdout}\n{res.stderr}".strip())

    issues = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        dg_name = parts[0].upper()
        try:
            used_pct = float(parts[3])
        except (ValueError, IndexError):
            continue
        if used_pct >= threshold:
            issues.append(f"{dg_name}: {used_pct}%")

    if issues:
        return _crit(host_name, name,
                     f"ASM diskgroup vượt {threshold}%: {', '.join(issues)}",
                     raw=output, details={"overloaded": issues})
    return _ok(host_name, name, f"Tất cả ASM diskgroup < {threshold}%", raw=output)


# ── Bước 11: Kiểm tra Alert Log ──────────────────────────────────────────────

def check_alert_log(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.alert_log"
    name = step_name(key)
    keywords = get("thresholds.alert_log_error_keywords", ["ORA-", "TNS-", "Errors in file"])
    instance_name = ctx["instance"]

    # Lấy đường dẫn alert log qua sqlplus
    res_path = commands.run(client, key, "dest", ctx)
    alert_dir = None
    for line in res_path.stdout.splitlines():
        if "background_dump_dest" in line.lower():
            parts = line.split()
            if len(parts) >= 3:
                alert_dir = parts[-1].strip()
                break

    if not alert_dir:
        # Fallback đường dẫn phổ biến
        alert_dir = f"/u01/app/oracle/diag/rdbms/{instance_name.lower()}/{instance_name.lower()}/trace"

    # Đọc alert log rồi lọc theo từ khoá lỗi
    res_scan = commands.run(client, key, "scan", {**ctx, "alert_dir": alert_dir})
    if not res_scan.success:
        return _err(host_name, name,
                    f"Không đọc được alert log ({res_scan.command}): {res_scan.stderr[:200]}")

    error_lines = [
        line for line in res_scan.stdout.splitlines()
        if any(kw in line for kw in keywords)
    ][-20:]

    if error_lines:
        return _warn(host_name, name,
                     f"Phát hiện {len(error_lines)} dòng lỗi trong alert log",
                     raw="\n".join(error_lines), details={"error_lines": error_lines[:10]})
    return _ok(host_name, name, f"Alert log không có dòng chứa từ khoá lỗi ({', '.join(keywords)})")


# ── Entry point: chạy tất cả checks cho Oracle ───────────────────────────────

# Bước 1 & 2 chạy trên mọi node; bước 3–11 chỉ chạy trên node đầu tiên
ALL_NODE_STEPS = [
    ("oracle.system_info", check_system_info),
    ("oracle.disk_space", check_disk_space),
]
FIRST_NODE_STEPS = [
    ("oracle.crs_status", check_crs_status),
    ("oracle.ocr", check_ocr),
    ("oracle.voting_disk", check_voting_disk),
    ("oracle.archive_switch", check_archive_switch),
    ("oracle.free_tablespace", check_free_tablespace),
    ("oracle.unusable_indexes", check_unusable_indexes),
    ("oracle.invalid_objects", check_invalid_objects),
    ("oracle.asm_diskgroup", check_asm_diskgroup),
    ("oracle.alert_log", check_alert_log),
]


def run_all(commands: Optional[BuiltinCommandSet] = None) -> List[CheckResult]:
    """Chạy toàn bộ 11 bước kiểm tra Oracle trên cả 2 node DB."""
    if commands is None:
        commands = BuiltinCommandStore().load_command_set()
    cfg = get("oracle", {})
    hosts = cfg.get("hosts", [])
    results: List[CheckResult] = []

    for i, host_cfg in enumerate(hosts):
        host_name = host_cfg.get("name", host_cfg["ip"])
        steps = ALL_NODE_STEPS + (FIRST_NODE_STEPS if i == 0 else [])
        steps = [fn for key, fn in steps if commands.is_enabled(key)]
        if not steps:
            continue
        ctx = {
            "host_name": host_name,
            "host_ip": host_cfg["ip"],
            "instance": host_cfg.get("instance", "simo1"),
            "asm_instance": host_cfg.get("asm_instance", "+ASM1"),
        }

        logger.info(f"Oracle: kết nối {host_name} ({host_cfg['ip']})")
        try:
            with ssh_connect(host_cfg) as client:
                for fn in steps:
                    results.append(fn(client, host_name, commands, ctx))
        except ConnectionError as e:
            logger.error(f"Oracle: không kết nối được {host_name}: {e}")
            results.append(_err(host_name, "Kết nối SSH", str(e)))
        except Exception as e:
            logger.exception(f"Oracle: lỗi không xác định trên {host_name}")
            results.append(_err(host_name, "Lỗi không xác định", str(e)))

    return results
