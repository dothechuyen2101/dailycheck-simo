"""
Dynamic Check Runners – Thực thi các bước check dựa trên tham số do Dashboard
truyền vào, không dùng giá trị nghiệp vụ mặc định trong code.

Các runner ở đây nhận `system_plan` dạng runtime (từ `runtime_plan.build_runtime_plan`)
và trả về `List[CheckResult]`.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable, List, Optional

from src.logger import CheckResult
from src.ssh_manager import SSHClient, ssh_connect

logger = logging.getLogger("dailycheck.runner")


# ── Helpers chung ─────────────────────────────────────────────────────────

def _result(system: str, host: str, name: str, status: str,
            message: str, raw: str = "", details: dict | None = None) -> CheckResult:
    return CheckResult(system, host, name, status, message, raw, details or {})


def _ok(system, host, name, msg, raw="", details=None):
    return _result(system, host, name, "OK", msg, raw, details)


def _warn(system, host, name, msg, raw="", details=None):
    return _result(system, host, name, "WARNING", msg, raw, details)


def _crit(system, host, name, msg, raw="", details=None):
    return _result(system, host, name, "CRITICAL", msg, raw, details)


def _err(system, host, name, msg):
    return _result(system, host, name, "ERROR", msg)


def _require(params: dict, key: str) -> Any:
    if params is None or key not in params or params[key] in (None, "", []):
        raise ValueError(f"Thiếu tham số bắt buộc '{key}'")
    return params[key]


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    # Hỗ trợ nhập nhiều dòng trong textarea
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _as_int(value) -> int:
    return int(float(value))


def _as_float(value) -> float:
    return float(value)


def _host_cfg_for_ssh(host: dict) -> dict:
    """Chuyển host từ runtime plan sang cấu trúc `ssh_connect` chấp nhận."""
    return {
        "ip": host["ip"],
        "ssh": {
            "port": host.get("ssh_port", 22),
            "username": host["username"],
            "key_path": host.get("key_path"),
            "password": host.get("password"),
        },
    }


def _parse_disk_df(output: str, threshold_pct: float, excluded: set[str]) -> list[str]:
    """Parse output của df, trả về danh sách mount vượt ngưỡng."""
    issues: list[str] = []
    lines = [l for l in output.splitlines() if l.strip()]
    if not lines:
        return issues
    # Bỏ header
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        # df -T có thêm cột Type ở vị trí thứ 2 → mount vẫn nằm ở cột cuối,
        # còn %used ở cột -2. Xử lý bằng cách lấy từ cuối.
        mount = parts[-1]
        pct_str = None
        for p in reversed(parts[:-1]):
            if p.endswith("%"):
                pct_str = p.replace("%", "")
                break
        if pct_str is None:
            continue
        if mount.lower() in excluded:
            continue
        try:
            pct = float(pct_str)
        except ValueError:
            continue
        if pct >= threshold_pct:
            issues.append(f"{mount} ({pct:g}%)")
    return issues


def _contains_all(output: str, markers: Iterable[str]) -> list[str]:
    """Trả về list marker KHÔNG tìm thấy trong output."""
    missing: list[str] = []
    low = output.lower()
    for m in markers:
        if m and m.lower() not in low:
            missing.append(m)
    return missing


def _contains_any(output: str, markers: Iterable[str]) -> Optional[str]:
    low = output.lower()
    for m in markers:
        if m and m.lower() in low:
            return m
    return None


# ── Check steps – triển khai theo registry ────────────────────────────────

def _step_system_info(system, host, client, params):
    name = params.get("step_name") or "Thông số hệ thống"
    cmds = _as_list(_require(params, "commands"))
    outputs = []
    ok = True
    for cmd in cmds:
        res = client.run(cmd)
        outputs.append(f"$ {cmd}\n{res.stdout or res.stderr}")
        if not res.success:
            ok = False
    raw = "\n\n".join(outputs)
    if ok:
        return _ok(system, host["name"], name, "Lệnh hệ thống chạy thành công", raw=raw)
    return _err(system, host["name"], name, "Có lệnh hệ thống thất bại")


def _step_disk_space(system, host, client, params):
    name = params.get("step_name") or "Kiểm tra phân vùng đĩa"
    command = _require(params, "command")
    threshold = _as_float(_require(params, "threshold_pct"))
    excluded = {m.lower() for m in _as_list(params.get("excluded_mounts"))}

    res = client.run(command)
    if not res.success and not res.stdout:
        return _err(system, host["name"], name,
                    f"Lệnh {command!r} thất bại: {res.stderr[:200]}")
    issues = _parse_disk_df(res.stdout, threshold, excluded)
    if issues:
        return _crit(system, host["name"], name,
                     f"Phân vùng vượt {threshold:g}%: {', '.join(issues)}",
                     raw=res.stdout, details={"overloaded": issues})
    return _ok(system, host["name"], name,
               f"Tất cả phân vùng < {threshold:g}%", raw=res.stdout)


def _step_crs_status(system, host, client, params):
    name = params.get("step_name") or "CRS Status"
    command = _require(params, "command")
    fallback = params.get("fallback_command")
    allowed_offline = [a.lower() for a in _as_list(params.get("allowed_offline"))]

    res = client.run(command)
    if not res.success and fallback:
        res = client.run(fallback)
    if not res.success and not res.stdout:
        return _err(system, host["name"], name,
                    f"Không lấy được trạng thái CRS: {res.stderr[:200]}")

    issues = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or line.lower().startswith("name") or line.startswith("---"):
            continue
        svc = parts[0].lower()
        target = parts[2].upper() if len(parts) > 2 else ""
        state = parts[3].upper() if len(parts) > 3 else ""
        if any(allowed in svc for allowed in allowed_offline):
            continue
        if target != state:
            issues.append(f"{parts[0]} target={target}/state={state}")
    if issues:
        return _crit(system, host["name"], name,
                     f"CRS không đồng bộ: {', '.join(issues)}",
                     raw=res.stdout, details={"issues": issues})
    return _ok(system, host["name"], name, "Tất cả CRS resource OK", raw=res.stdout)


def _step_ocr(system, host, client, params):
    name = params.get("step_name") or "OCR Check"
    command = _require(params, "command")
    expected = _as_list(_require(params, "expected_contains"))

    res = client.run(command)
    output = (res.stdout or "") + (res.stderr or "")
    missing = _contains_all(output, expected)
    if missing:
        return _crit(system, host["name"], name,
                     f"OCR thiếu chuỗi yêu cầu: {', '.join(missing)}",
                     raw=output, details={"missing": missing})
    return _ok(system, host["name"], name, "OCR OK", raw=output)


def _step_voting_disk(system, host, client, params):
    name = params.get("step_name") or "Voting Disk"
    command = _require(params, "command")
    required = _as_int(_require(params, "required_online"))

    res = client.run(command)
    output = (res.stdout or "") + (res.stderr or "")
    online_count = len(re.findall(r"\bONLINE\b", output, re.IGNORECASE))
    located = re.search(r"Located\s+(\d+)\s+voting disk", output, re.IGNORECASE)
    located_count = int(located.group(1)) if located else online_count
    if online_count < required or located_count < required:
        return _crit(system, host["name"], name,
                     f"Chỉ có {online_count} ONLINE voting disk (yêu cầu {required})",
                     raw=output, details={"online": online_count, "required": required})
    return _ok(system, host["name"], name,
               f"{online_count}/{located_count} voting disk ONLINE", raw=output)


def _oracle_sid(host: dict, params: dict, field_key: str = "oracle_sid_field") -> str:
    field = _require(params, field_key)
    extra = host.get("extra") or {}
    if field not in extra or not extra[field]:
        raise ValueError(
            f"Host '{host.get('name')}' chưa khai báo extra.{field}"
        )
    return str(extra[field])


def _run_sqlplus(client: SSHClient, sid: str, sql: str, as_sys: str = "sysdba",
                 timeout: int = 120):
    """Chạy sqlplus với SQL nhập từ Dashboard."""
    script = sql.strip()
    if not script.lower().rstrip().endswith("exit;"):
        script = script + "\nEXIT;"
    cmd = f"export ORACLE_SID={sid}; echo \"{script}\" | sqlplus -S / as {as_sys}"
    return client.run(cmd, timeout=timeout)


def _step_archive_switch(system, host, client, params):
    name = params.get("step_name") or "Archive Switch"
    sid = _oracle_sid(host, params)
    sql = _require(params, "sql")
    expected = _as_list(_require(params, "expected_contains"))

    res = _run_sqlplus(client, sid, sql)
    output = (res.stdout or "") + (res.stderr or "")
    missing = _contains_all(output, expected)
    if missing:
        # Nếu có ORA-... coi là CRITICAL
        if "ORA-" in output:
            ora = re.findall(r"ORA-\d+[^\n]*", output)
            return _crit(system, host["name"], name,
                         f"Archive switch lỗi: {'; '.join(ora[:3])}",
                         raw=output)
        return _warn(system, host["name"], name,
                     f"Chưa thấy chuỗi: {', '.join(missing)}", raw=output)
    return _ok(system, host["name"], name, "Archive switch OK", raw=output)


def _step_free_tablespace(system, host, client, params):
    name = params.get("step_name") or "Free Tablespace"
    sid = _oracle_sid(host, params)
    sql = _require(params, "sql")
    sys_names = {s.upper() for s in _as_list(_require(params, "system_tablespaces"))}
    sys_min = _as_float(_require(params, "system_min_free_pct"))
    undo_prefix = str(_require(params, "undo_prefix")).upper()
    undo_min_mb = _as_float(_require(params, "undo_min_total_mb"))
    default_min = _as_float(_require(params, "default_min_free_pct"))

    res = _run_sqlplus(client, sid, sql, timeout=120)
    output = res.stdout or ""

    issues = []
    for line in output.splitlines():
        parts = line.replace(",", "").split()
        if len(parts) < 5:
            continue
        ts_name = parts[0].upper()
        try:
            total_mb = float(parts[1])
            pct_free = float(parts[4])
        except ValueError:
            continue
        if ts_name in sys_names:
            if pct_free < sys_min:
                issues.append(f"{ts_name}: %Free={pct_free:.1f}% < {sys_min}%")
        elif ts_name.startswith(undo_prefix):
            if total_mb < undo_min_mb:
                issues.append(f"{ts_name}: Total={total_mb:.0f}MB < {undo_min_mb}MB")
        else:
            if pct_free < default_min:
                issues.append(f"{ts_name}: %Free={pct_free:.1f}% < {default_min}%")

    if issues:
        return _crit(system, host["name"], name,
                     f"Tablespace vượt ngưỡng: {'; '.join(issues)}",
                     raw=output, details={"issues": issues})
    return _ok(system, host["name"], name, "Tất cả tablespace đạt ngưỡng", raw=output)


def _check_empty_sql(system, host, client, params, label_default):
    name = params.get("step_name") or label_default
    sid = _oracle_sid(host, params)
    sql = _require(params, "sql")
    marker = _require(params, "expected_empty_marker")

    res = _run_sqlplus(client, sid, sql, timeout=120)
    output = res.stdout or ""
    if str(marker).lower() in output.lower():
        return _ok(system, host["name"], name, f"Không có dòng dữ liệu ({marker})", raw=output)
    stmts = [l for l in output.splitlines() if l.strip() and not l.strip().lower().startswith("sql>")]
    return _warn(system, host["name"], name,
                 f"Phát hiện {len(stmts)} dòng cần xử lý",
                 raw=output, details={"count": len(stmts)})


def _step_unusable_indexes(system, host, client, params):
    return _check_empty_sql(system, host, client, params, "Unusable Indexes")


def _step_invalid_objects(system, host, client, params):
    return _check_empty_sql(system, host, client, params, "Invalid Objects")


def _step_asm_diskgroup(system, host, client, params):
    name = params.get("step_name") or "ASM Disk Group"
    sid = _oracle_sid(host, params, field_key="asm_sid_field")
    sql = _require(params, "sql")
    threshold = _as_float(_require(params, "threshold_pct"))

    res = _run_sqlplus(client, sid, sql, as_sys="sysasm", timeout=60)
    output = res.stdout or ""
    issues = []
    for line in output.splitlines():
        parts = line.replace(",", "").split()
        if len(parts) < 4:
            continue
        dg_name = parts[0].upper()
        try:
            used_pct = float(parts[-1])
        except ValueError:
            continue
        if used_pct >= threshold:
            issues.append(f"{dg_name}: {used_pct}%")
    if issues:
        return _crit(system, host["name"], name,
                     f"ASM diskgroup vượt {threshold:g}%: {', '.join(issues)}",
                     raw=output, details={"overloaded": issues})
    return _ok(system, host["name"], name,
               f"Tất cả ASM diskgroup < {threshold:g}%", raw=output)


def _step_alert_log(system, host, client, params):
    name = params.get("step_name") or "Alert Log"
    sid = _oracle_sid(host, params)
    path_sql = _require(params, "path_sql")
    path_field = _require(params, "path_field")
    tail_template = _require(params, "tail_command_template")
    keywords = _as_list(_require(params, "keywords"))

    res_path = _run_sqlplus(client, sid, path_sql, timeout=30)
    alert_dir = None
    for line in (res_path.stdout or "").splitlines():
        if path_field.lower() in line.lower():
            parts = line.split()
            if len(parts) >= 3:
                alert_dir = parts[-1].strip()
                break
    if not alert_dir:
        return _err(system, host["name"], name,
                    f"Không xác định được đường dẫn alert log từ field '{path_field}'")

    kw_pattern = "|".join(re.escape(k) for k in keywords) or "."
    cmd = tail_template.format(
        alert_dir=alert_dir,
        instance=sid,
        keywords=kw_pattern,
    )
    res = client.run(cmd, timeout=45)
    output = res.stdout.strip()
    if output:
        lines = output.splitlines()
        return _warn(system, host["name"], name,
                     f"Phát hiện {len(lines)} dòng nghi lỗi trong alert log",
                     raw=output, details={"preview": lines[:10]})
    return _ok(system, host["name"], name,
               "Alert log không có dòng khớp keyword", raw=cmd)


def _step_service_status(system, host, client, params):
    name = params.get("step_name") or "Service Status"
    command = _require(params, "command")
    expected = _as_list(_require(params, "expected_contains"))

    res = client.run(command)
    output = (res.stdout or "") + (res.stderr or "")
    hit = _contains_any(output, expected)
    if hit:
        return _ok(system, host["name"], name, f"Service OK (chứa '{hit}')", raw=output)
    if any(m.lower() in output.lower() for m in ("inactive", "failed")):
        return _crit(system, host["name"], name, "Service không chạy", raw=output)
    return _warn(system, host["name"], name,
                 "Không thấy chuỗi yêu cầu trong output", raw=output)


def _step_hdfs_report(system, host, client, params):
    name = params.get("step_name") or "HDFS Report"
    command = _require(params, "command")
    safe_mode_error = _require(params, "safe_mode_error")
    min_live = _as_int(_require(params, "min_live_datanodes"))
    threshold = _as_float(_require(params, "dfs_used_threshold_pct"))

    res = client.run(command, timeout=60)
    output = (res.stdout or "") + (res.stderr or "")

    if safe_mode_error.lower() in output.lower():
        return _crit(system, host["name"], name,
                     f"HDFS Safe Mode: '{safe_mode_error}' xuất hiện", raw=output)

    live_match = re.search(r"Live datanodes\s*\((\d+)\)", output, re.IGNORECASE)
    dead_match = re.search(r"Dead datanodes\s*\((\d+)\)", output, re.IGNORECASE)
    live_count = int(live_match.group(1)) if live_match else 0
    dead_count = int(dead_match.group(1)) if dead_match else 0

    if live_count < min_live:
        return _crit(system, host["name"], name,
                     f"Live DataNodes={live_count} < {min_live}",
                     raw=output, details={"live": live_count, "dead": dead_count})

    used_match = re.search(r"DFS Used%:\s*([\d.]+)%", output)
    used_pct = float(used_match.group(1)) if used_match else 0
    if used_pct >= threshold:
        return _warn(system, host["name"], name,
                     f"DFS Used {used_pct}% ≥ {threshold}%",
                     raw=output, details={"dfs_used_pct": used_pct})
    return _ok(system, host["name"], name,
               f"HDFS OK – Live={live_count}, DFS Used={used_pct}%",
               raw=output)


def _step_docker_containers(system, host, client, params):
    name = params.get("step_name") or "Docker Containers"
    ps_command = _require(params, "ps_command")
    running_marker = _require(params, "running_marker")
    svc_command = params.get("service_command")

    res = client.run(ps_command)
    if not res.success and not res.stdout:
        return _err(system, host["name"], name,
                    f"Lệnh {ps_command!r} thất bại: {res.stderr[:200]}")
    output = res.stdout
    not_running = []
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        cname, cstatus = parts[0], parts[1]
        if running_marker.lower() not in cstatus.lower():
            not_running.append(f"{cname} ({cstatus})")

    svc_issues = []
    if svc_command:
        res_svc = client.run(svc_command)
        if res_svc.success:
            for line in res_svc.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) < 2:
                    continue
                svc_name, replicas = parts[0], parts[1]
                m = re.match(r"(\d+)/(\d+)", replicas)
                if m and m.group(1) != m.group(2):
                    svc_issues.append(f"{svc_name}: {replicas}")

    issues = not_running + svc_issues
    if issues:
        return _warn(system, host["name"], name,
                     f"Docker issues: {', '.join(issues)}",
                     raw=output, details={"issues": issues})
    return _ok(system, host["name"], name, "Tất cả Docker container đang chạy", raw=output)


# ── Step registry (function dispatcher) ──────────────────────────────────

STEP_HANDLERS: dict[tuple[str, str], Callable] = {
    ("oracle", "system_info"): _step_system_info,
    ("oracle", "disk_space"): _step_disk_space,
    ("oracle", "crs_status"): _step_crs_status,
    ("oracle", "ocr"): _step_ocr,
    ("oracle", "voting_disk"): _step_voting_disk,
    ("oracle", "archive_switch"): _step_archive_switch,
    ("oracle", "free_tablespace"): _step_free_tablespace,
    ("oracle", "unusable_indexes"): _step_unusable_indexes,
    ("oracle", "invalid_objects"): _step_invalid_objects,
    ("oracle", "asm_diskgroup"): _step_asm_diskgroup,
    ("oracle", "alert_log"): _step_alert_log,

    ("mongodb", "system_info"): _step_system_info,
    ("mongodb", "disk_space"): _step_disk_space,
    ("mongodb", "service_status"): _step_service_status,

    ("hadoop", "system_info"): _step_system_info,
    ("hadoop", "disk_space"): _step_disk_space,
    ("hadoop", "hdfs_report"): _step_hdfs_report,

    ("application", "system_info"): _step_system_info,
    ("application", "disk_space"): _step_disk_space,
    ("application", "service_status"): _step_service_status,
    ("application", "docker_containers"): _step_docker_containers,
}


def _dispatch(system_type: str, step_id: str, system_name: str, host: dict,
              client: SSHClient, params: dict) -> CheckResult:
    handler = STEP_HANDLERS.get((system_type, step_id))
    if not handler:
        return _err(system_name, host["name"], step_id,
                    f"Không có handler cho step {system_type}/{step_id}")
    try:
        return handler(system_name, host, client, params)
    except ValueError as e:
        return _err(system_name, host["name"], params.get("step_name") or step_id, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("Lỗi khi chạy step %s/%s: %s", system_type, step_id, e)
        return _err(system_name, host["name"], params.get("step_name") or step_id, str(e))


# ── Runner theo system ────────────────────────────────────────────────────

def run_system_plan(system_plan: dict) -> List[CheckResult]:
    """Thực thi toàn bộ step của một system trong runtime plan."""
    system_type = system_plan["system_type"]
    system_name = system_plan["system_name"]
    steps = system_plan.get("steps", [])

    # Gom targets theo host để mở SSH 1 lần / host
    host_to_steps: dict[int, list[dict]] = {}
    host_by_id: dict[int, dict] = {}
    for step in steps:
        for host in step.get("targets", []):
            hid = int(host["id"])
            host_by_id[hid] = host
            host_to_steps.setdefault(hid, []).append(step)

    results: List[CheckResult] = []
    for hid, host_steps in host_to_steps.items():
        host = host_by_id[hid]
        host_cfg = _host_cfg_for_ssh(host)
        logger.info("Runner [%s] kết nối %s (%s)", system_name, host["name"], host["ip"])
        try:
            with ssh_connect(host_cfg) as client:
                for step in host_steps:
                    params = dict(step.get("params") or {})
                    params.setdefault("step_name", step.get("step_name"))
                    res = _dispatch(system_type, step["step_id"], system_name,
                                    host, client, params)
                    results.append(res)
        except ConnectionError as e:
            logger.error("SSH lỗi %s: %s", host["name"], e)
            results.append(_err(system_name, host["name"], "Kết nối SSH", str(e)))
        except Exception as e:  # noqa: BLE001
            logger.exception("Lỗi không xác định trên %s", host["name"])
            results.append(_err(system_name, host["name"], "Lỗi không xác định", str(e)))

    return results
