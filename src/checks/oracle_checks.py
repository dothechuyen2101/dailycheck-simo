"""
Oracle Check Module – 11 bước kiểm tra Oracle RAC theo kịch bản Daily Check SIMO.
"""
import logging
import re
from collections import Counter
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


# ── Bước 3: Kiểm tra CRS (crsctl stat res -t / crs_stat -t) ──────────────────

CRS_TARGETS = {"ONLINE", "OFFLINE"}
CRS_SECTION_LINES = {"local resources", "cluster resources"}


def _command_not_found(res: CommandResult) -> bool:
    stderr = res.stderr.lower()
    return (
        res.exit_code == 127
        or "not found" in stderr
        or "no such file" in stderr
        or "does not exist" in stderr  # AIX: "A file or directory in the path name does not exist."
    )


def parse_crs_issues(output: str, allowed_offline: List[str]) -> List[str]:
    """
    Trả về các resource có Target ≠ State. Đọc được 2 định dạng:
    - crsctl stat res -t (12c+): tên resource trên 1 dòng, các dòng thụt lề bên dưới
      là "[số thứ tự] TARGET STATE [SERVER] [STATE DETAILS]".
    - crs_stat -t (11g): mỗi dòng "Name Type Target State Host".

    Bỏ qua slot thừa: instance không gán server nào trong khi resource đã ONLINE trên đủ số node
    (vd. ora.asmgroup của Flex ASM 19c có cardinality 3 nên trên cụm 2 node slot 3 luôn ONLINE/OFFLINE).
    """
    allowed = [a.lower() for a in allowed_offline]
    entries = []  # (resource, target, state, server)
    resource = None
    # crsctl in theo cột cố định: vị trí cột Server / State details lấy từ dòng tiêu đề,
    # vì cột State details có khoảng trắng ("Instance Shutdown,STABLE") và Server có thể trống
    server_col = details_col = None

    for line in output.expandtabs(8).splitlines():
        stripped = line.strip()
        if stripped.startswith("Name ") and "Server" in line:
            server_col = line.index("Server")
            details_col = line.find("State details")
            continue
        if (not stripped or stripped.startswith("---") or stripped.startswith("Name ")
                or stripped.lower() in CRS_SECTION_LINES):
            continue
        parts = stripped.split()
        if not line[0].isspace():
            if len(parts) >= 4 and parts[2].upper() in CRS_TARGETS:
                entries.append((parts[0], parts[2].upper(), parts[3].upper(), parts[4] if len(parts) > 4 else ""))
                resource = None
            else:
                resource = parts[0]
            continue
        if resource is None:
            continue
        if parts[0].isdigit():
            parts = parts[1:]
        # Dòng tiếp nối của cột State details (bị ngắt dòng) không bắt đầu bằng ONLINE/OFFLINE
        if len(parts) >= 2 and parts[0].upper() in CRS_TARGETS:
            if server_col is not None:
                end = details_col if details_col > server_col else None
                server = line[server_col:end].strip().split(" ")[0] if len(line) > server_col else ""
            else:
                server = parts[2] if len(parts) > 3 else ""
            entries.append((resource, parts[0].upper(), parts[1].upper(), server))

    servers = {server for _, _, _, server in entries if server}
    online = Counter(res_name for res_name, _, state, _ in entries if state == "ONLINE")
    issues = []
    for res_name, target, state, server in entries:
        if target == state or any(a in res_name.lower() for a in allowed):
            continue
        if not server and servers and online[res_name] >= len(servers):
            continue
        where = f", server={server}" if server else ""
        issues.append(f"{res_name} (target={target}, state={state}{where})")
    return issues


def check_crs_status(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.crs_status"
    name = step_name(key)
    allowed_offline = get("oracle.crs_allowed_offline", [
        "ora.gsd", "ora.oc4j", "ora....bkup.db",
    ])
    res = commands.run(client, key, "main", ctx)
    if not res.success and _command_not_found(res) and commands.has_command(key, "fallback"):
        # Thử lệnh dự phòng (vd. crs_stat -t trên 11g)
        main_res = res
        res = commands.run(client, key, "fallback", ctx)
        if not res.success:
            return _err(host_name, name,
                        f"{main_res.command} thất bại: {main_res.stderr[:200]} | "
                        f"lệnh dự phòng {res.command} thất bại: {res.stderr[:200]}")
    if not res.success:
        return _err(host_name, name, f"{res.command} thất bại: {res.stderr[:200]}")

    # Cho phép offline các service ngoại lệ trong oracle.crs_allowed_offline
    issues = parse_crs_issues(res.stdout, allowed_offline)
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
        hint = ""
        if online_count == total_located:
            # Diskgroup EXTERNAL redundancy chỉ có 1 voting disk (NORMAL: 3, HIGH: 5)
            hint = (" – nếu diskgroup chứa voting disk dùng EXTERNAL redundancy thì đây là thiết kế, "
                    "chỉnh thresholds.voting_disk_required")
        return _crit(host_name, name,
                     f"{online_count}/{total_located} voting disk ONLINE (yêu cầu {required}){hint}",
                     raw=output, details={"online": online_count, "located": total_located, "required": required})
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

def parse_sqlplus_table(output: str) -> List[dict]:
    """
    Tách bảng SQL*Plus thành list {tiêu đề cột: giá trị}. Ranh giới cột lấy từ dòng gạch ngang
    dưới tiêu đề (tiêu đề có thể nhiều từ như "% Free" hoặc bị SQL*Plus cắt theo độ rộng cột);
    mỗi từ được gán vào cột mà nó nằm trong. Bỏ qua tiêu đề lặp lại sau mỗi trang.
    SQL*Plus trên Unix mặc định SET TAB ON (thay khoảng trắng bằng TAB, mốc 8 ký tự) nên phải
    expandtabs trước khi tính vị trí cột – hiển thị thì giống hệt nhưng vị trí ký tự bị lệch.
    """
    lines = output.expandtabs(8).splitlines()
    dash_idx = next(
        (i for i, line in enumerate(lines)
         if i > 0 and line.strip() and set(line.strip()) <= {"-", " "} and " " in line.strip()),
        None,
    )
    if dash_idx is None:
        return []
    spans = [(m.start(), m.end()) for m in re.finditer(r"-+", lines[dash_idx])]

    def split(line: str) -> List[str]:
        cells = [[] for _ in spans]
        for m in re.finditer(r"\S+", line):
            # cột có đoạn giao lớn nhất với từ; không giao thì lấy cột gần nhất
            overlaps = [min(m.end(), b) - max(m.start(), a) for a, b in spans]
            best = max(range(len(spans)), key=lambda k: (overlaps[k], -abs(spans[k][0] - m.start())))
            cells[best].append(m.group(0))
        return [" ".join(c) for c in cells]

    header_line = lines[dash_idx - 1]
    headers = split(header_line)
    rows = []
    for line in lines[dash_idx + 1:]:
        stripped = line.strip()
        if not stripped or stripped == header_line.strip() or set(stripped) <= {"-", " "}:
            continue
        rows.append(dict(zip(headers, split(line))))
    return rows


def _column(headers: List[str], *prefixes: str, exact: tuple = ()) -> Optional[str]:
    for header in headers:
        key = re.sub(r"[^A-Z]", "", header.upper())
        if key in exact or any(key.startswith(p) for p in prefixes):
            return header
    return None


def _number(value) -> Optional[float]:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_tablespace_rows(output: str) -> List[dict]:
    """
    Đọc kết quả truy vấn tablespace → list {name, type, max_mb, pct_free}.
    Cột được tìm theo tên tiêu đề nên dùng được cả SQL của DBA (TABLESPACE_NAME, TYPE, MAX_SIZE_GB,
    FREE_MAX_PCT...) lẫn SQL cũ (Tablespace Name, Total/Max MBytes, % Free). Không có tiêu đề thì đọc
    theo vị trí: TÊN TOTAL_MB USED FREE PCT_FREE.
    """
    rows = []
    table = parse_sqlplus_table(output)
    # Không có dòng tiêu đề (SET HEADING OFF): đọc theo vị trí TÊN TOTAL_MB USED FREE PCT_FREE
    headers = list(table[0]) if table else []
    name_col = _column(headers, "TABLESPACE", exact=("NAME",)) or (headers[0] if headers else None)
    pct_free_col = _column(headers, "FREEMAX", "PCTFREE", "FREEPCT", exact=("FREE",))
    pct_used_col = _column(headers, "USEDMAX", "PCTUSED", "USEDPCT")
    size_col = _column(headers, "MAXSIZE", "MAXMB", "MAXGB", "TOTAL", exact=("MBYTES",))
    type_col = _column(headers, "TYPE", "CONTENTS")
    if table and not (name_col and (pct_free_col or pct_used_col)):
        # Có bảng nhưng không thấy cột % free (vd. bản ghi bị SQL*Plus ngắt thành nhiều dòng vì LINESIZE nhỏ):
        # không đoán theo vị trí để tránh đọc nhầm cột
        return []
    if table:
        size_key = re.sub(r"[^A-Z]", "", (size_col or "").upper())
        size_factor = 1 if "MB" in size_key else 1024  # cột GB (vd. MAX_SIZE_GB) → MB
        for row in table:
            if pct_free_col:
                pct_free = _number(row.get(pct_free_col))
            else:
                used = _number(row.get(pct_used_col))
                pct_free = None if used is None else 100 - used
            ts_name = (row.get(name_col) or "").strip().upper()
            if pct_free is None or not ts_name or " " in ts_name:
                continue
            size = _number(row.get(size_col)) if size_col else None
            rows.append({
                "name": ts_name,
                "type": (row.get(type_col) or "").strip().upper() if type_col else "",
                "max_mb": None if size is None else size * size_factor,
                "pct_free": pct_free,
            })
        return rows

    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        total_mb, pct_free = _number(parts[1]), _number(parts[4])
        if total_mb is None or pct_free is None:
            continue
        rows.append({"name": parts[0].upper(), "type": "", "max_mb": total_mb, "pct_free": pct_free})
    return rows


def check_free_tablespace(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.free_tablespace"
    name = step_name(key)
    thresholds = get("thresholds.tablespace", {})
    sys_min_pct = thresholds.get("system_min_free_pct", 3)
    undo_min_mb = thresholds.get("undotbs_min_total_mb", 400)
    default_min_pct = thresholds.get("default_min_free_pct", 15)
    temp_min_pct = thresholds.get("temp_min_free_pct", default_min_pct)

    res = commands.run(client, key, "main", ctx)
    output = res.stdout
    failure = _sql_failure(res)
    if failure:
        return _err(host_name, name, f"Truy vấn tablespace thất bại: {failure}",
                    raw=f"{res.stdout}\n{res.stderr}".strip())

    rows = parse_tablespace_rows(output)
    if not rows:
        # Không đọc được dòng nào thì không được báo OK (vd. SQL đổi thứ tự cột, dòng bị ngắt vì thiếu SET LINESIZE)
        return _err(host_name, name,
                    "Không đọc được dữ liệu tablespace từ output – SQL cần có cột tên tablespace và % free "
                    "(vd. FREE_MAX_PCT), và SET LINESIZE đủ rộng để mỗi tablespace nằm trên 1 dòng",
                    raw=output)

    issues = []
    for row in rows:
        ts_name, ts_type, pct_free, max_mb = row["name"], row["type"], row["pct_free"], row["max_mb"]
        if ts_name in ("SYSTEM", "SYSAUX", "EXAMPLE"):
            if pct_free < sys_min_pct:
                issues.append(f"{ts_name}: %Free={pct_free:.1f}% < {sys_min_pct}%")
        elif ts_type == "UNDO" or (not ts_type and ts_name.startswith("UNDOTBS")):
            # % free của UNDO không có ý nghĩa (extent hết hạn vẫn tính là đã dùng) – chỉ kiểm tra dung lượng
            if max_mb is not None and max_mb < undo_min_mb:
                issues.append(f"{ts_name}: Total={max_mb:.0f}MB < {undo_min_mb}MB")
        elif ts_type == "TEMPORARY":
            if pct_free < temp_min_pct:
                issues.append(f"{ts_name}: %Free={pct_free:.1f}% < {temp_min_pct}%")
        elif pct_free < default_min_pct:
            issues.append(f"{ts_name}: %Free={pct_free:.1f}% < {default_min_pct}%")

    if issues:
        return _crit(host_name, name,
                     f"Tablespace vượt ngưỡng: {'; '.join(issues)}",
                     raw=output, details={"issues": issues})
    return _ok(host_name, name, f"Tất cả {len(rows)} tablespace đều đạt ngưỡng", raw=output)


def _sql_statements(output: str, pattern: str) -> List[str]:
    """
    Các dòng dữ liệu bắt đầu bằng câu lệnh ALTER (trả về phần khớp group 1).
    Không đếm dòng tiêu đề cột SQL*Plus in ra (vd. 'ALTER'||OBJECT_TYPE||...||'COMPILE;'),
    vì dòng đó bắt đầu bằng dấu nháy, và tiêu đề lặp lại sau mỗi trang.
    """
    regex = re.compile(rf"^{pattern}", re.IGNORECASE)
    lines = output.splitlines()
    found = []
    i = 0
    while i < len(lines):
        stmt = lines[i].lstrip()
        if stmt.lower().startswith("alter "):
            # SQL*Plus ngắt dòng đúng tại LINESIZE (kể cả giữa tên object): ghép nguyên văn các dòng
            # tiếp nối cho tới khi câu lệnh kết thúc bằng ";"
            while (not stmt.rstrip().endswith(";") and i + 1 < len(lines) and lines[i + 1].strip()
                   and not lines[i + 1].lstrip().lower().startswith("alter ")):
                i += 1
                stmt += lines[i]
            m = regex.match(stmt.strip())
            if m:
                found.append(m.group(1))
        i += 1
    return found


def _preview(items: List[str], limit: int = 3) -> str:
    more = f" (+{len(items) - limit})" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


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
    rebuild_stmts = _sql_statements(output, r"alter\s+index\s+(\S+)")
    if rebuild_stmts:
        return _warn(host_name, name,
                     f"Phát hiện {len(rebuild_stmts)} unusable index cần rebuild: {_preview(rebuild_stmts)}",
                     raw=output, details={"count": len(rebuild_stmts), "objects": rebuild_stmts})
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
    compile_stmts = _sql_statements(output, r"alter\s+(.+?)\s+compile\b")
    if compile_stmts:
        return _warn(host_name, name,
                     f"Phát hiện {len(compile_stmts)} invalid object cần compile: {_preview(compile_stmts)}",
                     raw=output, details={"count": len(compile_stmts), "objects": compile_stmts})
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

def parse_alert_dir(output: str) -> Optional[str]:
    """
    Thư mục chứa alert log từ output sqlplus:
    - mặc định: `select value from v$diag_info where name = 'Diag Trace'` → một dòng đường dẫn;
    - lệnh cũ `show parameter background_dump_dest`: SQL*Plus ngắt cột VALUE sau 30 ký tự
      nên phải ghép các dòng tiếp nối.
    """
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if "background_dump_dest" in line.lower():
            parts = line.split()
            if len(parts) < 3:
                return None
            value = parts[-1]
            for nxt in lines[i + 1:]:
                if nxt[:1].isspace() and len(nxt.split()) == 1:
                    value += nxt.strip()
                else:
                    break
            return value
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("/") and " " not in stripped:
            return stripped
    return None


def check_alert_log(client: SSHClient, host_name: str, commands: BuiltinCommandSet, ctx: dict) -> CheckResult:
    key = "oracle.alert_log"
    name = step_name(key)
    keywords = get("thresholds.alert_log_error_keywords", ["ORA-", "TNS-", "Errors in file"])
    ignore = get("thresholds.alert_log_ignore_keywords", []) or []
    instance_name = ctx["instance"]

    # Lấy thư mục alert log qua sqlplus
    res_path = commands.run(client, key, "dest", ctx)
    alert_dir = parse_alert_dir(res_path.stdout) if res_path.success else None

    if not alert_dir:
        # Fallback theo cấu trúc ADR: diag/rdbms/<db_unique_name>/<instance>/trace.
        # RAC: db_unique_name thường là tên instance bỏ số cuối (simo1 → simo)
        db_unique_name = ctx.get("db_unique_name") or re.sub(r"\d+$", "", instance_name) or instance_name
        alert_dir = f"/u01/app/oracle/diag/rdbms/{db_unique_name.lower()}/{instance_name}/trace"

    # Đọc alert log rồi lọc theo từ khoá lỗi
    res_scan = commands.run(client, key, "scan", {**ctx, "alert_dir": alert_dir})
    if not res_scan.success:
        return _err(host_name, name,
                    f"Không đọc được alert log ({res_scan.command}): {res_scan.stderr[:200]}")

    error_lines = [
        line for line in res_scan.stdout.splitlines()
        if any(kw in line for kw in keywords) and not any(ig in line for ig in ignore)
    ]

    if error_lines:
        # Thống kê theo mã lỗi để báo cáo cho biết lỗi gì, không chỉ số dòng
        codes = Counter(re.findall(r"\b(?:ORA|TNS)-\d+", "\n".join(error_lines)))
        summary = ", ".join(f"{code} ×{count}" for code, count in codes.most_common(5))
        recent = error_lines[-20:]
        return _warn(host_name, name,
                     f"Phát hiện {len(error_lines)} dòng lỗi trong alert log" + (f": {summary}" if summary else ""),
                     raw="\n".join(recent),
                     details={"error_lines": recent[-10:], "error_codes": dict(codes)})
    return _ok(host_name, name, f"Alert log không có dòng chứa từ khoá lỗi ({', '.join(keywords)})")


# ── Dò môi trường Oracle (GRID_HOME, ORACLE_HOME, user chạy DB) ─────────────

ENV_KEYS = ("grid_home", "oracle_home", "oracle_user")
GRID_PROCESS_RE = re.compile(r"(/\S+)/bin/(?:ocssd|crsd|ohasd|evmd)\.bin\b")


def parse_oracle_environment(ps_output: str, oratab: str, instance: str) -> dict:
    """
    Lấy grid_home, oracle_home, oracle_user từ output `ps -eo user,args` và /etc/oratab.
    Chỉ trả về các giá trị tìm được.
    """
    env = {}
    m = GRID_PROCESS_RE.search(ps_output)
    if m:
        env["grid_home"] = m.group(1)
    pmon_re = re.compile(rf"\bora_pmon_{re.escape(instance)}\b", re.IGNORECASE)
    for line in ps_output.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and pmon_re.search(parts[1]):
            env["oracle_user"] = parts[0]
            break

    # /etc/oratab: SID:ORACLE_HOME:Y|N
    entries = []
    for line in oratab.splitlines():
        line = line.split("#", 1)[0].strip()
        fields = line.split(":")
        if len(fields) >= 2 and fields[0] and fields[1]:
            entries.append((fields[0], fields[1]))
    if "grid_home" not in env:
        asm = [home for sid, home in entries if sid.upper().startswith("+ASM")]
        if asm:
            env["grid_home"] = asm[0]
    db_name = re.sub(r"\d+$", "", instance)
    wanted = {instance.lower(), db_name.lower()}
    homes = [home for sid, home in entries if sid.lower() in wanted]
    if not homes:
        # Chỉ có 1 database trong oratab thì dùng luôn
        db_entries = [home for sid, home in entries if not sid.startswith(("+", "-"))]
        homes = db_entries if len(set(db_entries)) == 1 else []
    if homes:
        env["oracle_home"] = homes[0]
    return env


def parse_crs_database_resources(output: str, instance: str) -> dict:
    """
    Đọc `crsctl stat res -p` (các resource ora.database.type) và lấy ORACLE_HOME, DB_UNIQUE_NAME,
    owner của database chứa instance. Một host có thể chạy nhiều database (vd. simo1 và mid1).
    """
    db_name = re.sub(r"\d+$", "", instance).lower()
    for block in re.split(r"\n\s*\n", output.strip()):
        attrs = {}
        for line in block.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                attrs[k.strip()] = v.strip()
        if attrs.get("TYPE", "ora.database.type") != "ora.database.type":
            continue
        instances = {v.lower() for k, v in attrs.items() if "INST_NAME" in k and v}
        names = {attrs.get("DB_UNIQUE_NAME", "").lower(),
                 attrs.get("NAME", "").lower().removeprefix("ora.").removesuffix(".db")}
        if instance.lower() not in instances and db_name not in names:
            continue
        env = {}
        home = attrs.get("ORACLE_HOME", "")
        if home.startswith("/"):
            env["oracle_home"] = home
        if attrs.get("DB_UNIQUE_NAME"):
            env["db_unique_name"] = attrs["DB_UNIQUE_NAME"]
        owner = re.search(r"owner:([^:,]+):", attrs.get("ACL", ""))
        if owner:
            env["oracle_user"] = owner.group(1)
        return env
    return {}


def resolve_environment(client, commands: BuiltinCommandSet, host_cfg: dict, cfg: dict, ctx: dict) -> dict:
    """Giá trị trong config được ưu tiên; thiếu thì dò trên host."""
    configured = {k: host_cfg.get(k) or cfg.get(k) for k in ENV_KEYS}
    discovered = {}
    key = "oracle.environment"
    if not all(configured.values()) and commands.is_enabled(key):
        res_ps = commands.run(client, key, "ps", ctx)
        res_tab = commands.run(client, key, "oratab", ctx)
        discovered = parse_oracle_environment(
            res_ps.stdout if res_ps.success else "",
            res_tab.stdout if res_tab.success else "",
            ctx["instance"],
        )
        grid_home = configured["grid_home"] or discovered.get("grid_home")
        if not (configured["oracle_home"] or discovered.get("oracle_home")) and grid_home:
            # Không có /etc/oratab (vd. AIX): lấy ORACLE_HOME từ cấu hình resource database
            res_crs = commands.run(client, key, "crs_resources", {**ctx, "grid_home": grid_home})
            if res_crs.success:
                crs_env = parse_crs_database_resources(res_crs.stdout, ctx["instance"])
                discovered = {**crs_env, **discovered}
    env = {k: configured[k] or discovered.get(k, "") for k in ENV_KEYS}
    env["oracle_user"] = env["oracle_user"] or "oracle"
    if not ctx.get("db_unique_name") and discovered.get("db_unique_name"):
        env["db_unique_name"] = discovered["db_unique_name"]
    logger.info(
        "Oracle %s: GRID_HOME=%s ORACLE_HOME=%s user=%s (config: %s, dò được: %s)",
        ctx["host_name"], env["grid_home"] or "?", env["oracle_home"] or "?", env["oracle_user"],
        [k for k in ENV_KEYS if configured[k]], sorted(discovered),
    )
    return env


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
            "db_unique_name": host_cfg.get("db_unique_name", cfg.get("db_unique_name", "")),
            **{k: "" for k in ENV_KEYS},
        }
        # Chỉ các bước 3–11 cần GRID_HOME/ORACLE_HOME
        needs_env = any(fn not in {f for _, f in ALL_NODE_STEPS} for fn in steps)

        logger.info(f"Oracle: kết nối {host_name} ({host_cfg['ip']})")
        try:
            with ssh_connect(host_cfg) as client:
                if needs_env:
                    ctx.update(resolve_environment(client, commands, host_cfg, cfg, ctx))
                for fn in steps:
                    results.append(fn(client, host_name, commands, ctx))
        except ConnectionError as e:
            logger.error(f"Oracle: không kết nối được {host_name}: {e}")
            results.append(_err(host_name, "Kết nối SSH", str(e)))
        except Exception as e:
            logger.exception(f"Oracle: lỗi không xác định trên {host_name}")
            results.append(_err(host_name, "Lỗi không xác định", str(e)))

    return results
