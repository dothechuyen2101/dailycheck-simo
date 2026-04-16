"""
Oracle Check Module – 11 bước kiểm tra Oracle RAC theo kịch bản Daily Check SIMO.
"""
import logging
import re
from typing import List

from src.config_manager import get
from src.logger import CheckResult
from src.ssh_manager import SSHClient, ssh_connect

logger = logging.getLogger("dailycheck.oracle")

SYSTEM = "Oracle"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(host, name, msg, raw="", details=None) -> CheckResult:
    return CheckResult(SYSTEM, host, name, "OK", msg, raw, details or {})

def _warn(host, name, msg, raw="", details=None) -> CheckResult:
    return CheckResult(SYSTEM, host, name, "WARNING", msg, raw, details or {})

def _crit(host, name, msg, raw="", details=None) -> CheckResult:
    return CheckResult(SYSTEM, host, name, "CRITICAL", msg, raw, details or {})

def _err(host, name, msg) -> CheckResult:
    return CheckResult(SYSTEM, host, name, "ERROR", msg)


# ── Bước 1: Kiểm tra thông số hệ thống (date + hostname) ─────────────────────

def check_system_info(client: SSHClient, host_name: str) -> CheckResult:
    name = "Bước 1 – Thông số hệ thống"
    res_date = client.run("date")
    res_host = client.run("hostname")
    if not res_date.success or not res_host.success:
        return _err(host_name, name, "Không thể lấy date/hostname")
    return _ok(host_name, name,
               f"date={res_date.stdout.strip()} | hostname={res_host.stdout.strip()}",
               raw=f"date: {res_date.stdout}\nhostname: {res_host.stdout}")


# ── Bước 2: Kiểm tra phân vùng đĩa ───────────────────────────────────────────

def check_disk_space(client: SSHClient, host_name: str) -> CheckResult:
    name = "Bước 2 – Phân vùng đĩa"
    threshold = get("thresholds.disk_usage_max_pct", 90)
    res = client.run("df -k")
    if not res.success:
        return _err(host_name, name, f"Lệnh df -k thất bại: {res.stderr}")

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


# ── Bước 3: Kiểm tra CRS (crs_stat -t) ───────────────────────────────────────

def check_crs_status(client: SSHClient, host_name: str) -> CheckResult:
    name = "Bước 3 – CRS Status"
    allowed_offline = get("oracle.crs_allowed_offline", [
        "ora.gsd", "ora.oc4j", "ora....bkup.db",
    ])
    res = client.run("crs_stat -t")
    if not res.success and "No such file" in res.stderr:
        # Thử grid home path
        res = client.run("/u01/app/19.3.0/grid/bin/crs_stat -t")
    if not res.success:
        return _err(host_name, name, f"crs_stat thất bại: {res.stderr[:200]}")

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

def check_ocr(client: SSHClient, host_name: str) -> CheckResult:
    name = "Bước 4 – OCR Check"
    res = client.run("ocrcheck")
    if not res.success and not res.stdout:
        return _err(host_name, name, f"ocrcheck thất bại: {res.stderr[:200]}")
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

def check_voting_disk(client: SSHClient, host_name: str) -> CheckResult:
    name = "Bước 5 – Voting Disk"
    required = get("thresholds.voting_disk_required", 3)
    res = client.run("crsctl query css votedisk")
    if not res.success and not res.stdout:
        return _err(host_name, name, f"crsctl query css votedisk thất bại: {res.stderr[:200]}")

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

def check_archive_switch(client: SSHClient, host_name: str, instance_name: str) -> CheckResult:
    name = "Bước 6 – Archive Switch"
    # Thiết lập ORACLE_SID và chạy sqlplus
    cmd = (
        f"export ORACLE_SID={instance_name}; "
        f"echo 'alter system archive log current;' | sqlplus -S / as sysdba"
    )
    res = client.run(cmd)
    output = res.stdout + res.stderr
    if "System altered" in output:
        return _ok(host_name, name, "Archive switch thành công: System altered", raw=output)
    if "ORA-" in output:
        ora_errors = re.findall(r"ORA-\d+[^\n]*", output)
        return _crit(host_name, name,
                     f"Archive switch thất bại: {'; '.join(ora_errors[:3])}",
                     raw=output)
    return _warn(host_name, name,
                 "Kết quả archive switch không xác định, kiểm tra thủ công",
                 raw=output)


# ── Bước 7: Kiểm tra Free Tablespace ────────────────────────────────────────

TABLESPACE_SQL = """
COLUMN pct_free FORMAT 999.99 HEADING "% Free"
COLUMN name FORMAT A30 HEADING "Tablespace Name"
COLUMN mbytes FORMAT 99999999 HEADING "Total MBytes"
COLUMN used FORMAT 99999999 HEADING "Used Mbytes"
COLUMN free FORMAT 99999999 HEADING "Free Mbytes"
SELECT
  fs.tablespace_name name,
  df.totalspace mbytes,
  (df.totalspace - fs.freespace) used,
  fs.freespace free,
  100 * (fs.freespace / df.totalspace) pct_free
FROM
  (SELECT tablespace_name, ROUND(SUM(bytes) / 1048576) TotalSpace
   FROM dba_data_files GROUP BY tablespace_name) df,
  (SELECT tablespace_name, ROUND(SUM(bytes) / 1048576) FreeSpace
   FROM dba_free_space GROUP BY tablespace_name) fs
WHERE df.tablespace_name = fs.tablespace_name(+);
EXIT;
"""


def check_free_tablespace(client: SSHClient, host_name: str, instance_name: str) -> CheckResult:
    name = "Bước 7 – Free Tablespace"
    thresholds = get("thresholds.tablespace", {})
    sys_min_pct = thresholds.get("system_min_free_pct", 3)
    undo_min_mb = thresholds.get("undotbs_min_total_mb", 400)
    default_min_pct = thresholds.get("default_min_free_pct", 15)

    cmd = (
        f"export ORACLE_SID={instance_name}; "
        f"echo \"{TABLESPACE_SQL.strip()}\" | sqlplus -S / as sysdba"
    )
    res = client.run(cmd, timeout=90)
    output = res.stdout

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

UNUSABLE_IDX_SQL = """
select ' alter index '||owner || '.' || index_name || ' rebuild ; '
  from dba_indexes where STATUS = 'UNUSABLE'
   union all
      select ' alter index '||INDEX_OWNER || '.' || index_name || ' rebuild  partition ' ||partition_name || '; '
         from dba_ind_partitions where STATUS = 'UNUSABLE'
          union all
             select ' alter index '||INDEX_OWNER || '.' || index_name || ' rebuild  subpartition ' ||subpartition_name || '; '
                from dba_ind_subpartitions where STATUS = 'UNUSABLE';
EXIT;
"""


def check_unusable_indexes(client: SSHClient, host_name: str, instance_name: str) -> CheckResult:
    name = "Bước 8 – Unusable Indexes"
    cmd = (
        f"export ORACLE_SID={instance_name}; "
        f"echo \"{UNUSABLE_IDX_SQL.strip()}\" | sqlplus -S / as sysdba"
    )
    res = client.run(cmd, timeout=120)
    output = res.stdout
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

INVALID_OBJ_SQL = """
select 'ALTER ' || OBJECT_TYPE || ' ' ||
      OWNER || '.' || OBJECT_NAME || ' COMPILE;'
      from dba_objects
      where status = 'INVALID'
      and object_type in ('PACKAGE','FUNCTION','PROCEDURE','VIEW','SYNONYM')
and owner in (select username from dba_users where account_status ='OPEN');
EXIT;
"""


def check_invalid_objects(client: SSHClient, host_name: str, instance_name: str) -> CheckResult:
    name = "Bước 9 – Invalid Objects"
    cmd = (
        f"export ORACLE_SID={instance_name}; "
        f"echo \"{INVALID_OBJ_SQL.strip()}\" | sqlplus -S / as sysdba"
    )
    res = client.run(cmd, timeout=120)
    output = res.stdout
    if "no rows selected" in output.lower():
        return _ok(host_name, name, "Không có invalid object – no rows selected", raw=output)
    compile_stmts = [l for l in output.splitlines() if "compile" in l.lower()]
    if compile_stmts:
        return _warn(host_name, name,
                     f"Phát hiện {len(compile_stmts)} invalid object cần compile",
                     raw=output, details={"count": len(compile_stmts)})
    return _ok(host_name, name, "Không có invalid object", raw=output)


# ── Bước 10: Kiểm tra ASM Disk Group ────────────────────────────────────────

ASM_SQL = """
select name, TOTAL_MB, FREE_MB, trunc(((TOTAL_MB-FREE_MB)/TOTAL_MB)*100,2) "%used"
from v$asm_diskgroup;
EXIT;
"""


def check_asm_diskgroup(client: SSHClient, host_name: str) -> CheckResult:
    name = "Bước 10 – ASM Disk Group"
    threshold = get("thresholds.asm_usage_max_pct", 90)
    # ASM instance thường là +ASM1 hoặc +ASM2
    cmd = (
        "export ORACLE_SID=+ASM1; "
        f"echo \"{ASM_SQL.strip()}\" | sqlplus -S / as sysasm"
    )
    res = client.run(cmd, timeout=60)
    output = res.stdout

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

def check_alert_log(client: SSHClient, host_name: str, instance_name: str) -> CheckResult:
    name = "Bước 11 – Alert Log"
    keywords = get("thresholds.alert_log_error_keywords", ["ORA-", "TNS-", "Errors in file"])

    # Lấy đường dẫn alert log qua sqlplus
    cmd_path = (
        f"export ORACLE_SID={instance_name}; "
        "echo \"show parameter background_dump_dest;EXIT;\" | sqlplus -S / as sysdba"
    )
    res_path = client.run(cmd_path, timeout=30)
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

    # Tìm và scan alert log
    kw_pattern = "|".join(re.escape(k) for k in keywords)
    cmd_scan = (
        f"tail -1000 {alert_dir}/alert_{instance_name}.log 2>/dev/null "
        f"| egrep '{kw_pattern}' | tail -20"
    )
    res_scan = client.run(cmd_scan, timeout=30)
    output = res_scan.stdout.strip()

    if output:
        line_count = len(output.splitlines())
        return _warn(host_name, name,
                     f"Phát hiện {line_count} dòng lỗi trong alert log",
                     raw=output, details={"error_lines": output.splitlines()[:10]})
    return _ok(host_name, name, "Alert log không có lỗi ORA-/TNS- trong 1000 dòng cuối")


# ── Entry point: chạy tất cả checks cho Oracle ───────────────────────────────

def run_all(alert_manager=None) -> List[CheckResult]:
    """Chạy toàn bộ 11 bước kiểm tra Oracle trên cả 2 node DB."""
    cfg = get("oracle", {})
    hosts = cfg.get("hosts", [])
    results: List[CheckResult] = []

    for i, host_cfg in enumerate(hosts):
        host_name = host_cfg.get("name", host_cfg["ip"])
        instance = host_cfg.get("instance", "simo1")

        logger.info(f"Oracle: kết nối {host_name} ({host_cfg['ip']})")
        try:
            with ssh_connect(host_cfg) as client:
                # Bước 1 & 2: chạy trên cả 2 node
                results.append(check_system_info(client, host_name))
                results.append(check_disk_space(client, host_name))

                # Bước 3–11: chỉ chạy trên node đầu tiên (hoặc cả 2 nếu cần)
                if i == 0:
                    results.append(check_crs_status(client, host_name))
                    results.append(check_ocr(client, host_name))
                    results.append(check_voting_disk(client, host_name))
                    results.append(check_archive_switch(client, host_name, instance))
                    results.append(check_free_tablespace(client, host_name, instance))
                    results.append(check_unusable_indexes(client, host_name, instance))
                    results.append(check_invalid_objects(client, host_name, instance))
                    results.append(check_asm_diskgroup(client, host_name))
                    results.append(check_alert_log(client, host_name, instance))
        except ConnectionError as e:
            logger.error(f"Oracle: không kết nối được {host_name}: {e}")
            results.append(_err(host_name, "Kết nối SSH", str(e)))
        except Exception as e:
            logger.exception(f"Oracle: lỗi không xác định trên {host_name}")
            results.append(_err(host_name, "Lỗi không xác định", str(e)))

        # Gửi alert ngay nếu có bất thường
        if alert_manager:
            for r in results:
                alert_manager.alert(r)

    return results
