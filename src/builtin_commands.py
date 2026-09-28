"""
Built-in Commands – lệnh mặc định của các check built-in (Oracle, MongoDB, Hadoop, Application)
và phần ghi đè do người dùng chỉnh sửa trên dashboard (lưu trong SQLite).

Logic parse output vẫn nằm trong src/checks/*_checks.py; module này chỉ quyết định
lệnh nào được chạy, SQL script gửi qua stdin, timeout và bước nào được bật.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.config_manager import get
from src.dynamic_modules import (
    TEMPLATE_VAR_RE,
    _coerce_bool,
    _validate_command_template,
    render_command_template,
)
from src.logger import ResultStore
from src.ssh_manager import CommandResult

logger = logging.getLogger("dailycheck.builtin_commands")

MAX_TIMEOUT_SEC = 3600

SYSTEM_LABELS = {
    "oracle": "Oracle",
    "mongodb": "MongoDB",
    "hadoop": "Hadoop",
    "application": "Application",
}

_BASE_VARIABLES = ("host_name", "host_ip")
SYSTEM_VARIABLES = {
    "oracle": _BASE_VARIABLES + ("instance", "asm_instance"),
    "mongodb": _BASE_VARIABLES + ("service_name",),
    "hadoop": _BASE_VARIABLES,
    "application": _BASE_VARIABLES + ("app_name", "service_name"),
}


@dataclass(frozen=True)
class BuiltinCommandDef:
    key: str
    label: str
    command: str
    script: str = ""
    timeout_sec: Optional[int] = None
    optional: bool = False
    extra_vars: tuple = ()

    @property
    def has_script(self) -> bool:
        return bool(self.script)

    @property
    def default_timeout(self) -> int:
        return int(self.timeout_sec or get("general.command_timeout", 60))


@dataclass(frozen=True)
class BuiltinStepDef:
    key: str
    system: str
    name: str
    commands: tuple
    output_hint: str = ""

    @property
    def variables(self) -> tuple:
        return SYSTEM_VARIABLES[self.system]

    def command(self, cmd_key: str) -> BuiltinCommandDef:
        for cmd in self.commands:
            if cmd.key == cmd_key:
                return cmd
        raise KeyError(f"{self.key}.{cmd_key}")


# ── Lệnh mặc định ─────────────────────────────────────────────────────────────

SQLPLUS_SYSDBA = "export ORACLE_SID={{instance}}; sqlplus -S / as sysdba"

ARCHIVE_SWITCH_SQL = """
alter system archive log current;
EXIT;
""".strip()

TABLESPACE_SQL = """
SET PAGESIZE 1000 LINESIZE 200
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
""".strip()

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
""".strip()

INVALID_OBJ_SQL = """
select 'ALTER ' || OBJECT_TYPE || ' ' ||
      OWNER || '.' || OBJECT_NAME || ' COMPILE;'
      from dba_objects
      where status = 'INVALID'
      and object_type in ('PACKAGE','FUNCTION','PROCEDURE','VIEW','SYNONYM')
and owner in (select username from dba_users where account_status ='OPEN');
EXIT;
""".strip()

ASM_SQL = """
select name, TOTAL_MB, FREE_MB, trunc(((TOTAL_MB-FREE_MB)/TOTAL_MB)*100,2) "%used"
from v$asm_diskgroup;
EXIT;
""".strip()

ALERT_LOG_DEST_SQL = """
show parameter background_dump_dest;
EXIT;
""".strip()

DISK_HINT = "Output dạng df: cột áp chót là Use% (vd 85%), cột cuối là Mounted on. Phân vùng ≥ thresholds.disk_usage_max_pct → CRITICAL."
SYSTEMCTL_HINT = "Output chứa 'active (running)' → OK; chứa 'inactive' hoặc 'failed' → CRITICAL; còn lại → WARNING."
SQL_ERROR_HINT = "Có ORA-/SP2- hoặc exit code khác 0 → ERROR."


def _system_info_step(system: str) -> BuiltinStepDef:
    return BuiltinStepDef(
        key=f"{system}.system_info",
        system=system,
        name="Bước 1 – Thông số hệ thống",
        commands=(
            BuiltinCommandDef("date", "Ngày giờ", "date"),
            BuiltinCommandDef("hostname", "Hostname", "hostname"),
        ),
        output_hint="Hiển thị nguyên output của 2 lệnh; lệnh nào lỗi (exit code khác 0) → ERROR.",
    )


def _disk_space_step(system: str, command: str) -> BuiltinStepDef:
    return BuiltinStepDef(
        key=f"{system}.disk_space",
        system=system,
        name="Bước 2 – Phân vùng đĩa",
        commands=(BuiltinCommandDef("main", "Dung lượng phân vùng", command),),
        output_hint=DISK_HINT,
    )


BUILTIN_STEPS: tuple = (
    # ── Oracle ──
    _system_info_step("oracle"),
    _disk_space_step("oracle", "df -k"),
    BuiltinStepDef(
        key="oracle.crs_status",
        system="oracle",
        name="Bước 3 – CRS Status",
        commands=(
            BuiltinCommandDef("main", "Lệnh chính", "crs_stat -t"),
            BuiltinCommandDef(
                "fallback", "Lệnh dự phòng (khi không tìm thấy lệnh chính, để trống = bỏ qua)",
                "/u01/app/19.3.0/grid/bin/crs_stat -t", optional=True,
            ),
        ),
        output_hint=(
            "Output dạng bảng crs_stat -t: cột 1 tên resource, cột 3 Target, cột 4 State. "
            "Target ≠ State → CRITICAL (trừ oracle.crs_allowed_offline)."
        ),
    ),
    BuiltinStepDef(
        key="oracle.ocr",
        system="oracle",
        name="Bước 4 – OCR Check",
        commands=(BuiltinCommandDef("main", "Kiểm tra OCR", "ocrcheck"),),
        output_hint=(
            "Thiếu 'integrity check succeeded' → CRITICAL; "
            "thiếu 'Cluster registry integrity check succeeded' → WARNING."
        ),
    ),
    BuiltinStepDef(
        key="oracle.voting_disk",
        system="oracle",
        name="Bước 5 – Voting Disk",
        commands=(BuiltinCommandDef("main", "Truy vấn voting disk", "crsctl query css votedisk"),),
        output_hint=(
            "Đếm số từ ONLINE và dòng 'Located N voting disk(s)'. "
            "Ít hơn thresholds.voting_disk_required → CRITICAL."
        ),
    ),
    BuiltinStepDef(
        key="oracle.archive_switch",
        system="oracle",
        name="Bước 6 – Archive Switch",
        commands=(BuiltinCommandDef("main", "sqlplus", SQLPLUS_SYSDBA, script=ARCHIVE_SWITCH_SQL),),
        output_hint="Output chứa 'System altered' → OK; có ORA- → CRITICAL; có SP2- hoặc exit code khác 0 → ERROR.",
    ),
    BuiltinStepDef(
        key="oracle.free_tablespace",
        system="oracle",
        name="Bước 7 – Free Tablespace",
        commands=(BuiltinCommandDef("main", "sqlplus", SQLPLUS_SYSDBA, script=TABLESPACE_SQL, timeout_sec=90),),
        output_hint=(
            "Mỗi dòng dữ liệu: TÊN TOTAL_MB USED_MB FREE_MB PCT_FREE (cột 2 và cột 5 là số). "
            "So với thresholds.tablespace. " + SQL_ERROR_HINT
        ),
    ),
    BuiltinStepDef(
        key="oracle.unusable_indexes",
        system="oracle",
        name="Bước 8 – Unusable Indexes",
        commands=(BuiltinCommandDef("main", "sqlplus", SQLPLUS_SYSDBA, script=UNUSABLE_IDX_SQL, timeout_sec=120),),
        output_hint=(
            "'no rows selected' → OK; mỗi dòng chứa 'alter index' tính là 1 index cần rebuild → WARNING. "
            + SQL_ERROR_HINT
        ),
    ),
    BuiltinStepDef(
        key="oracle.invalid_objects",
        system="oracle",
        name="Bước 9 – Invalid Objects",
        commands=(BuiltinCommandDef("main", "sqlplus", SQLPLUS_SYSDBA, script=INVALID_OBJ_SQL, timeout_sec=120),),
        output_hint=(
            "'no rows selected' → OK; mỗi dòng chứa 'compile' tính là 1 object cần compile → WARNING. "
            + SQL_ERROR_HINT
        ),
    ),
    BuiltinStepDef(
        key="oracle.asm_diskgroup",
        system="oracle",
        name="Bước 10 – ASM Disk Group",
        commands=(
            BuiltinCommandDef(
                "main", "sqlplus (sysasm)",
                "export ORACLE_SID={{asm_instance}}; sqlplus -S / as sysasm",
                script=ASM_SQL, timeout_sec=60,
            ),
        ),
        output_hint=(
            "Mỗi dòng dữ liệu: NAME TOTAL_MB FREE_MB %USED (cột 4 là % sử dụng). "
            "≥ thresholds.asm_usage_max_pct → CRITICAL. " + SQL_ERROR_HINT
        ),
    ),
    BuiltinStepDef(
        key="oracle.alert_log",
        system="oracle",
        name="Bước 11 – Alert Log",
        commands=(
            BuiltinCommandDef(
                "dest", "Lấy thư mục alert log", SQLPLUS_SYSDBA,
                script=ALERT_LOG_DEST_SQL, timeout_sec=30,
            ),
            BuiltinCommandDef(
                "scan", "Đọc alert log", "tail -1000 {{alert_dir}}/alert_{{instance}}.log",
                timeout_sec=30, extra_vars=("alert_dir",),
            ),
        ),
        output_hint=(
            "Lệnh 1: dòng chứa background_dump_dest, giá trị ở cột cuối → {{alert_dir}} "
            "(không có thì dùng /u01/app/oracle/diag/rdbms/<instance>/<instance>/trace). "
            "Lệnh 2: nội dung alert log; dòng chứa thresholds.alert_log_error_keywords → WARNING; "
            "lệnh lỗi → ERROR."
        ),
    ),
    # ── MongoDB ──
    _system_info_step("mongodb"),
    _disk_space_step("mongodb", "df -kh"),
    BuiltinStepDef(
        key="mongodb.service",
        system="mongodb",
        name="Bước 3 – Trạng thái mongod",
        commands=(BuiltinCommandDef("main", "Trạng thái service", "systemctl status {{service_name}}"),),
        output_hint=SYSTEMCTL_HINT,
    ),
    # ── Hadoop ──
    _system_info_step("hadoop"),
    _disk_space_step("hadoop", "df -T"),
    BuiltinStepDef(
        key="hadoop.hdfs_report",
        system="hadoop",
        name="Bước 3 – HDFS dfsadmin report",
        commands=(BuiltinCommandDef("main", "HDFS report", "hdfs dfsadmin -report", timeout_sec=60),),
        output_hint=(
            "'Safe mode is ON' hoặc 'Live datanodes (0)' → CRITICAL; 'Dead datanodes (N>0)' → WARNING; "
            "'DFS Used%: x%' ≥ thresholds.disk_usage_max_pct → WARNING."
        ),
    ),
    # ── Application ──
    _system_info_step("application"),
    _disk_space_step("application", "df -kh"),
    BuiltinStepDef(
        key="application.service",
        system="application",
        name="Bước 3 – Service",
        commands=(BuiltinCommandDef("main", "Trạng thái service", "systemctl status {{service_name}}"),),
        output_hint=SYSTEMCTL_HINT + " Chỉ chạy khi ứng dụng có khai báo service.",
    ),
    BuiltinStepDef(
        key="application.docker",
        system="application",
        name="Bước 3b – Docker Containers",
        commands=(
            BuiltinCommandDef("ps", "Danh sách container", r"docker ps --format '{{.Names}}\t{{.Status}}'"),
            BuiltinCommandDef(
                "service_ls", "Docker Swarm service",
                r"docker service ls --format '{{.Name}}\t{{.Replicas}}'",
            ),
        ),
        output_hint=(
            "Lệnh 1: mỗi dòng TÊN<tab>STATUS, status không chứa 'Up' → WARNING. "
            "Lệnh 2: mỗi dòng TÊN<tab>REPLICAS (vd 2/2), thiếu replica → WARNING; lệnh lỗi (không phải Swarm) → bỏ qua. "
            "Chỉ chạy khi ứng dụng có has_docker: true."
        ),
    ),
)

_STEP_INDEX = {step.key: step for step in BUILTIN_STEPS}


def get_step_def(step_key: str) -> BuiltinStepDef:
    try:
        return _STEP_INDEX[step_key]
    except KeyError:
        raise KeyError(step_key) from None


def step_name(step_key: str) -> str:
    return get_step_def(step_key).name


# ── Validate ──────────────────────────────────────────────────────────────────

def _validate_builtin_command(step: BuiltinStepDef, cmd: BuiltinCommandDef, command: str) -> None:
    if not command:
        if cmd.optional:
            return
        raise ValueError("lệnh không được để trống")
    _validate_command_template(command)
    allowed = set(step.variables) | set(cmd.extra_vars)
    unknown = sorted(set(TEMPLATE_VAR_RE.findall(command)) - allowed)
    if unknown:
        raise ValueError(
            f"biến không hỗ trợ: {', '.join('{{' + v + '}}' for v in unknown)}; "
            f"biến dùng được: {', '.join('{{' + v + '}}' for v in sorted(allowed))}"
        )


def validate_step_override(step: BuiltinStepDef, payload: Any) -> dict:
    """
    Chuẩn hoá payload sửa một bước. Chỉ giữ lại các giá trị khác mặc định.
    Trả về {"enabled": bool, "commands": {cmd_key: {command?, script?, timeout_sec?}}}.
    """
    if not isinstance(payload, dict):
        raise ValueError("Dữ liệu gửi lên không hợp lệ")
    commands_payload = payload.get("commands") or {}
    if not isinstance(commands_payload, dict):
        raise ValueError("'commands' phải là object")

    errors: list[str] = []
    known = {cmd.key for cmd in step.commands}
    unknown = sorted(set(commands_payload) - known)
    if unknown:
        errors.append(f"Lệnh không tồn tại trong bước: {', '.join(unknown)}")

    overrides: dict[str, dict] = {}
    for cmd in step.commands:
        data = commands_payload.get(cmd.key)
        if data is None:
            continue
        if not isinstance(data, dict):
            errors.append(f"{cmd.label}: dữ liệu không hợp lệ")
            continue
        diff: dict[str, Any] = {}

        if "command" in data:
            command = str(data.get("command") or "").strip()
            try:
                _validate_builtin_command(step, cmd, command)
            except ValueError as exc:
                errors.append(f"{cmd.label}: {exc}")
            else:
                if command != cmd.command:
                    diff["command"] = command

        if "script" in data:
            script = str(data.get("script") or "").replace("\r\n", "\n").strip()
            if not cmd.has_script:
                if script:
                    errors.append(f"{cmd.label}: lệnh này không dùng SQL script")
            elif not script:
                errors.append(f"{cmd.label}: SQL script không được để trống")
            elif script != cmd.script:
                diff["script"] = script

        raw_timeout = data.get("timeout_sec")
        if raw_timeout not in (None, ""):
            try:
                timeout = int(raw_timeout)
            except (TypeError, ValueError):
                errors.append(f"{cmd.label}: timeout phải là số nguyên")
            else:
                if not 1 <= timeout <= MAX_TIMEOUT_SEC:
                    errors.append(f"{cmd.label}: timeout phải trong khoảng 1–{MAX_TIMEOUT_SEC} giây")
                elif timeout != cmd.default_timeout:
                    diff["timeout_sec"] = timeout

        if diff:
            overrides[cmd.key] = diff

    if errors:
        raise ValueError("; ".join(errors))
    return {"enabled": _coerce_bool(payload.get("enabled", True)), "commands": overrides}


# ── Snapshot dùng khi chạy ────────────────────────────────────────────────────

def _effective_command(cmd: BuiltinCommandDef, override: dict | None) -> dict:
    override = override or {}
    return {
        "command": override.get("command", cmd.command),
        "script": override.get("script", cmd.script),
        "timeout_sec": int(override.get("timeout_sec", cmd.default_timeout)),
    }


class BuiltinCommandSet:
    """Lệnh hiệu lực (mặc định + ghi đè) tại thời điểm bắt đầu một lần chạy."""

    def __init__(self, overrides: dict | None = None):
        self._overrides = overrides or {}

    def is_enabled(self, step_key: str) -> bool:
        get_step_def(step_key)
        return bool(self._overrides.get(step_key, {}).get("enabled", True))

    def resolve(self, step_key: str, cmd_key: str) -> dict:
        cmd = get_step_def(step_key).command(cmd_key)
        override = self._overrides.get(step_key, {}).get("commands", {}).get(cmd_key)
        return _effective_command(cmd, override)

    def has_command(self, step_key: str, cmd_key: str) -> bool:
        return bool(self.resolve(step_key, cmd_key)["command"])

    def run(self, client, step_key: str, cmd_key: str, context: dict) -> CommandResult:
        """Render lệnh với context rồi chạy qua SSH. Lỗi render trả về CommandResult thất bại."""
        spec = self.resolve(step_key, cmd_key)
        template = spec["command"]
        if not template:
            return CommandResult("", "Lệnh trống", -1, False, "")
        # Chỉ sanitize các biến thực sự dùng trong lệnh
        used = set(TEMPLATE_VAR_RE.findall(template))
        try:
            rendered = render_command_template(
                template, {k: v for k, v in context.items() if k in used}
            )
        except ValueError as exc:
            return CommandResult("", f"Lệnh không hợp lệ: {exc}", -1, False, template)
        script = spec["script"]
        return client.run(
            rendered,
            timeout=spec["timeout_sec"],
            input=(script + "\n") if script else None,
        )


# ── SQLite store ──────────────────────────────────────────────────────────────

class BuiltinCommandStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = ResultStore(db_path=db_path).db_path
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS builtin_step_overrides (
                    step_key      TEXT PRIMARY KEY,
                    enabled       INTEGER NOT NULL DEFAULT 1,
                    commands_json TEXT NOT NULL DEFAULT '{}',
                    updated_at    TEXT
                )
            """)

    def _load_overrides(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT step_key, enabled, commands_json, updated_at FROM builtin_step_overrides"
            ).fetchall()
        overrides = {}
        for step_key, enabled, commands_json, updated_at in rows:
            if step_key not in _STEP_INDEX:
                continue
            try:
                commands = json.loads(commands_json or "{}")
            except json.JSONDecodeError:
                logger.warning("Bỏ qua override lỗi JSON cho bước %s", step_key)
                commands = {}
            overrides[step_key] = {
                "enabled": bool(enabled),
                "commands": commands if isinstance(commands, dict) else {},
                "updated_at": updated_at,
            }
        return overrides

    def load_command_set(self) -> BuiltinCommandSet:
        return BuiltinCommandSet(self._load_overrides())

    def list_steps(self) -> list[dict]:
        overrides = self._load_overrides()
        return [_step_to_dict(step, overrides.get(step.key)) for step in BUILTIN_STEPS]

    def get_step(self, step_key: str) -> dict:
        step = get_step_def(step_key)
        return _step_to_dict(step, self._load_overrides().get(step_key))

    def save_override(self, step_key: str, payload: Any) -> dict:
        step = get_step_def(step_key)
        normalized = validate_step_override(step, payload)
        with self._conn() as conn:
            if normalized["enabled"] and not normalized["commands"]:
                conn.execute("DELETE FROM builtin_step_overrides WHERE step_key = ?", (step_key,))
            else:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO builtin_step_overrides
                        (step_key, enabled, commands_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        step_key,
                        int(normalized["enabled"]),
                        json.dumps(normalized["commands"], ensure_ascii=False),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
        return self.get_step(step_key)

    def reset(self, step_key: str) -> dict:
        get_step_def(step_key)
        with self._conn() as conn:
            conn.execute("DELETE FROM builtin_step_overrides WHERE step_key = ?", (step_key,))
        return self.get_step(step_key)


def _step_to_dict(step: BuiltinStepDef, override: dict | None) -> dict:
    override = override or {}
    cmd_overrides = override.get("commands", {})
    commands = []
    for cmd in step.commands:
        cmd_override = cmd_overrides.get(cmd.key)
        commands.append({
            "key": cmd.key,
            "label": cmd.label,
            "optional": cmd.optional,
            "has_script": cmd.has_script,
            "variables": list(step.variables) + list(cmd.extra_vars),
            **_effective_command(cmd, cmd_override),
            "default": _effective_command(cmd, None),
            "is_modified": bool(cmd_override),
        })
    enabled = bool(override.get("enabled", True))
    return {
        "key": step.key,
        "system": step.system,
        "system_label": SYSTEM_LABELS[step.system],
        "name": step.name,
        "output_hint": step.output_hint,
        "enabled": enabled,
        "is_modified": (not enabled) or any(c["is_modified"] for c in commands),
        "updated_at": override.get("updated_at"),
        "commands": commands,
    }
