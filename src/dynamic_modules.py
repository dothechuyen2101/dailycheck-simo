"""
Dynamic module registry, validation, and execution.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from src.config_manager import get
from src.logger import CheckResult, ResultStore
from src.security import decrypt_secret, encrypt_secret

logger = logging.getLogger("dailycheck.dynamic_modules")

if TYPE_CHECKING:
    from src.ssh_manager import SSHClient

TIME_RE = re.compile(r"^\d{2}:\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TEMPLATE_VAR_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
SAFE_VALUE_PUNCTUATION = set(" ._-/@:+=,")
DISALLOWED_VALUE_CHARS = set(";&|`$()<>\"'\\\r\n\t")


@dataclass
class DynamicModuleHost:
    id: Optional[int]
    name: str
    ip: str
    port: int = 22
    username: str = ""
    auth_type: str = "password"
    encrypted_password: str = ""
    key_path: str = ""
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass
class DynamicModuleStep:
    id: Optional[int]
    name: str
    command: str
    timeout_sec: int = 60
    run_on_each_host: bool = True
    rules: dict[str, Any] = field(default_factory=dict)


@dataclass
class DynamicModule:
    id: Optional[int]
    name: str
    system_name: str
    enabled: bool
    timezone: str
    daily_time: str
    recipient_emails: list[str]
    template_key: str = "custom"
    hosts: list[DynamicModuleHost] = field(default_factory=list)
    steps: list[DynamicModuleStep] = field(default_factory=list)
    created_by: str = "admin"
    created_at: str = ""
    updated_at: str = ""


def _default_rules() -> dict[str, Any]:
    return {
        "nonzero_exit_status": "ERROR",
        "warning_keywords": [],
        "critical_keywords": [],
    }


MODULE_TEMPLATES: dict[str, dict[str, Any]] = {
    "oracle": {
        "label": "Oracle",
        "system_name": "Oracle",
        "host_variable_help": ["instance_name"],
        "steps": [
            {"name": "Bước 1 – Thông số hệ thống", "command": "printf 'date: '; date; printf 'hostname: '; hostname", "timeout_sec": 30, "run_on_each_host": True, "rules": _default_rules()},
            {"name": "Bước 2 – Phân vùng đĩa", "command": "df -k", "timeout_sec": 60, "run_on_each_host": True, "rules": {"nonzero_exit_status": "ERROR", "warning_keywords": [], "critical_keywords": [" 90% ", " 91% ", " 92% ", " 93% ", " 94% ", " 95% ", " 96% ", " 97% ", " 98% ", " 99% ", "100%"]}},
            {"name": "Bước 3 – CRS Status", "command": "crs_stat -t || /u01/app/19.3.0/grid/bin/crs_stat -t", "timeout_sec": 60, "run_on_each_host": False, "rules": {"nonzero_exit_status": "ERROR", "warning_keywords": [], "critical_keywords": [" OFFLINE ", "UNKNOWN", "INTERMEDIATE"]}},
            {"name": "Bước 4 – OCR Check", "command": "ocrcheck", "timeout_sec": 60, "run_on_each_host": False, "rules": {"nonzero_exit_status": "CRITICAL", "warning_keywords": [], "critical_keywords": ["failed", "corrupt"]}},
            {"name": "Bước 5 – Voting Disk", "command": "crsctl query css votedisk", "timeout_sec": 60, "run_on_each_host": False, "rules": {"nonzero_exit_status": "CRITICAL", "warning_keywords": [], "critical_keywords": ["OFFLINE", "0 voting", "1 voting", "2 voting"]}},
            {"name": "Bước 6 – Archive Switch", "command": "export ORACLE_SID={{instance_name}}; echo 'alter system archive log current;' | sqlplus -S / as sysdba", "timeout_sec": 90, "run_on_each_host": False, "rules": {"nonzero_exit_status": "CRITICAL", "warning_keywords": [], "critical_keywords": ["ORA-"]}},
            {"name": "Bước 7 – Free Tablespace", "command": "export ORACLE_SID={{instance_name}}; echo \"SELECT tablespace_name, ROUND(SUM(bytes)/1048576) total_mb FROM dba_data_files GROUP BY tablespace_name; EXIT;\" | sqlplus -S / as sysdba", "timeout_sec": 90, "run_on_each_host": False, "rules": {"nonzero_exit_status": "CRITICAL", "warning_keywords": ["SYSAUX", "SYSTEM", "UNDOTBS"], "critical_keywords": ["ORA-"]}},
            {"name": "Bước 8 – Unusable Indexes", "command": "export ORACLE_SID={{instance_name}}; echo \"select index_name from dba_indexes where status = 'UNUSABLE'; EXIT;\" | sqlplus -S / as sysdba", "timeout_sec": 120, "run_on_each_host": False, "rules": {"nonzero_exit_status": "WARNING", "warning_keywords": ["ALTER INDEX", "UNUSABLE"], "critical_keywords": ["ORA-"]}},
            {"name": "Bước 9 – Invalid Objects", "command": "export ORACLE_SID={{instance_name}}; echo \"select object_name from dba_objects where status = 'INVALID'; EXIT;\" | sqlplus -S / as sysdba", "timeout_sec": 120, "run_on_each_host": False, "rules": {"nonzero_exit_status": "WARNING", "warning_keywords": ["INVALID"], "critical_keywords": ["ORA-"]}},
            {"name": "Bước 10 – ASM Disk Group", "command": "export ORACLE_SID=+ASM1; echo \"select name, trunc(((TOTAL_MB-FREE_MB)/TOTAL_MB)*100,2) pct_used from v$asm_diskgroup; EXIT;\" | sqlplus -S / as sysasm", "timeout_sec": 60, "run_on_each_host": False, "rules": {"nonzero_exit_status": "CRITICAL", "warning_keywords": [], "critical_keywords": [" 90", " 91", " 92", " 93", " 94", " 95", " 96", " 97", " 98", " 99", "100"]}},
            {"name": "Bước 11 – Alert Log", "command": "tail -1000 /u01/app/oracle/diag/rdbms/{{instance_name}}/{{instance_name}}/trace/alert_{{instance_name}}.log 2>/dev/null | egrep 'ORA-|TNS-|Errors in file' | tail -20", "timeout_sec": 60, "run_on_each_host": False, "rules": {"nonzero_exit_status": "WARNING", "warning_keywords": ["ORA-", "TNS-", "Errors in file"], "critical_keywords": []}},
        ],
    },
    "mongodb": {
        "label": "MongoDB",
        "system_name": "MongoDB",
        "host_variable_help": ["service_name"],
        "steps": [
            {"name": "Bước 1 – Thông số hệ thống", "command": "printf 'date: '; date; printf 'hostname: '; hostname", "timeout_sec": 30, "run_on_each_host": True, "rules": _default_rules()},
            {"name": "Bước 2 – Phân vùng đĩa", "command": "df -kh", "timeout_sec": 60, "run_on_each_host": True, "rules": {"nonzero_exit_status": "ERROR", "warning_keywords": [], "critical_keywords": [" 90% ", " 91% ", " 92% ", " 93% ", " 94% ", " 95% ", " 96% ", " 97% ", " 98% ", " 99% ", "100%"]}},
            {"name": "Bước 3 – Trạng thái mongod", "command": "systemctl status {{service_name}}", "timeout_sec": 60, "run_on_each_host": True, "rules": {"nonzero_exit_status": "CRITICAL", "warning_keywords": ["inactive", "failed"], "critical_keywords": ["inactive", "failed"]}},
        ],
    },
    "hadoop": {
        "label": "Hadoop",
        "system_name": "Hadoop",
        "host_variable_help": ["is_namenode"],
        "steps": [
            {"name": "Bước 1 – Thông số hệ thống", "command": "printf 'date: '; date; printf 'hostname: '; hostname", "timeout_sec": 30, "run_on_each_host": True, "rules": _default_rules()},
            {"name": "Bước 2 – Phân vùng đĩa", "command": "df -T", "timeout_sec": 60, "run_on_each_host": True, "rules": {"nonzero_exit_status": "ERROR", "warning_keywords": [], "critical_keywords": [" 90% ", " 91% ", " 92% ", " 93% ", " 94% ", " 95% ", " 96% ", " 97% ", " 98% ", " 99% ", "100%"]}},
            {"name": "Bước 3 – HDFS dfsadmin report", "command": "[ \"{{is_namenode}}\" = \"true\" ] && hdfs dfsadmin -report || echo 'SKIPPED: not namenode'", "timeout_sec": 60, "run_on_each_host": True, "rules": {"nonzero_exit_status": "CRITICAL", "warning_keywords": ["Dead datanodes", "SKIPPED"], "critical_keywords": ["safe mode is ON", "No live datanodes"]}},
        ],
    },
    "application": {
        "label": "Application",
        "system_name": "Application",
        "host_variable_help": ["service_name", "check_docker"],
        "steps": [
            {"name": "Bước 1 – Thông số hệ thống", "command": "printf 'date: '; date; printf 'hostname: '; hostname", "timeout_sec": 30, "run_on_each_host": True, "rules": _default_rules()},
            {"name": "Bước 2 – Phân vùng đĩa", "command": "df -kh", "timeout_sec": 60, "run_on_each_host": True, "rules": {"nonzero_exit_status": "ERROR", "warning_keywords": [], "critical_keywords": [" 90% ", " 91% ", " 92% ", " 93% ", " 94% ", " 95% ", " 96% ", " 97% ", " 98% ", " 99% ", "100%"]}},
            {"name": "Bước 3 – Service", "command": "systemctl status {{service_name}}", "timeout_sec": 60, "run_on_each_host": True, "rules": {"nonzero_exit_status": "CRITICAL", "warning_keywords": ["inactive", "failed"], "critical_keywords": ["inactive", "failed"]}},
            {"name": "Bước 3b – Docker Containers", "command": "[ \"{{check_docker}}\" = \"true\" ] && docker ps --format '{{.Names}}\\t{{.Status}}' || echo 'SKIPPED: docker disabled'", "timeout_sec": 60, "run_on_each_host": True, "rules": {"nonzero_exit_status": "WARNING", "warning_keywords": ["SKIPPED", "Exited", "Created"], "critical_keywords": []}},
        ],
    },
    "custom": {"label": "Custom", "system_name": "Custom", "host_variable_help": [], "steps": []},
}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_recipients(value: Any) -> list[str]:
    if isinstance(value, list):
        recipients = value
    else:
        recipients = str(value or "").split(",")
    return [item.strip() for item in recipients if str(item).strip()]


def get_module_templates() -> dict[str, dict[str, Any]]:
    return json.loads(json.dumps(MODULE_TEMPLATES))


def build_module_from_template(template_key: str) -> dict[str, Any]:
    templates = get_module_templates()
    template = templates.get(template_key)
    if not template:
        raise KeyError(f"Unknown module template: {template_key}")
    return {
        "template_key": template_key,
        "system_name": template["system_name"],
        "hosts": [
            {
                "name": "",
                "ip": "",
                "port": 22,
                "username": "",
                "auth_type": "password",
                "key_path": "",
                "password": "",
                "variables": {key: "" for key in template.get("host_variable_help", [])},
            }
        ],
        "steps": template.get("steps", []),
    }


def validate_module_payload(payload: dict[str, Any], existing: Optional[DynamicModule] = None) -> dict[str, Any]:
    errors: list[str] = []

    name = str(payload.get("name", "")).strip()
    system_name = str(payload.get("system_name", "")).strip()
    template_key = str(payload.get("template_key") or "custom").strip().lower()
    timezone = str(payload.get("timezone") or get("general.timezone", "Asia/Ho_Chi_Minh")).strip()
    daily_time = str(payload.get("daily_time", "")).strip()
    recipient_emails = _normalize_recipients(payload.get("recipient_emails", []))
    enabled = _coerce_bool(payload.get("enabled", True))

    if not name:
        errors.append("name is required")
    if not system_name:
        errors.append("system_name is required")
    if template_key not in MODULE_TEMPLATES:
        errors.append("template_key must be one of the available templates")
    if not TIME_RE.match(daily_time):
        errors.append("daily_time must be in HH:MM format")
    else:
        hour, minute = [int(part) for part in daily_time.split(":")]
        if hour > 23 or minute > 59:
            errors.append("daily_time must be a valid 24-hour time")

    for email in recipient_emails:
        if not EMAIL_RE.match(email):
            errors.append(f"invalid recipient email: {email}")
    if not recipient_emails:
        errors.append("at least one recipient email is required")

    hosts: list[dict[str, Any]] = payload.get("hosts") or []
    if not hosts:
        errors.append("at least one host is required")
    normalized_hosts = []
    for index, host in enumerate(hosts, start=1):
        host_name = str(host.get("name", "")).strip()
        ip = str(host.get("ip", "")).strip()
        username = str(host.get("username", "")).strip()
        auth_type = str(host.get("auth_type") or "password").strip()
        key_path = str(host.get("key_path", "")).strip()
        raw_password = str(host.get("password", "") or "")
        encrypted_password = str(host.get("encrypted_password", "") or "")
        port = int(host.get("port") or 22)
        variables = host.get("variables") or {}
        if not isinstance(variables, dict):
            errors.append(f"host #{index}: variables must be an object")
            variables = {}

        if not host_name:
            errors.append(f"host #{index}: name is required")
        if not ip:
            errors.append(f"host #{index}: ip is required")
        if not username:
            errors.append(f"host #{index}: username is required")
        if auth_type not in {"password", "key"}:
            errors.append(f"host #{index}: auth_type must be password or key")
        if auth_type == "password":
            if raw_password:
                encrypted_password = encrypt_secret(raw_password)
            elif existing and host.get("id"):
                match = next((item for item in existing.hosts if item.id == host.get("id")), None)
                if match:
                    encrypted_password = match.encrypted_password
            if not encrypted_password:
                errors.append(f"host #{index}: password is required")
            key_path = ""
        else:
            if not key_path:
                errors.append(f"host #{index}: key_path is required for key auth")
            encrypted_password = ""

        normalized_hosts.append({
            "id": host.get("id"),
            "name": host_name,
            "ip": ip,
            "port": port,
            "username": username,
            "auth_type": auth_type,
            "key_path": key_path,
            "encrypted_password": encrypted_password,
            "variables": {
                str(key).strip(): str(value).strip()
                for key, value in variables.items()
                if str(key).strip()
            },
        })

    steps: list[dict[str, Any]] = payload.get("steps") or []
    if not steps:
        errors.append("at least one step is required")
    normalized_steps = []
    for index, step in enumerate(steps, start=1):
        step_name = str(step.get("name", "")).strip()
        command = str(step.get("command", "")).strip()
        timeout_sec = int(step.get("timeout_sec") or get("general.command_timeout", 60))
        run_on_each_host = _coerce_bool(step.get("run_on_each_host", True))
        rules = step.get("rules") or {}
        normalized_rules = {
            "nonzero_exit_status": str(
                rules.get("nonzero_exit_status")
                or _default_rules()["nonzero_exit_status"]
            ).strip().upper(),
            "warning_keywords": [str(item).strip() for item in rules.get("warning_keywords", []) if str(item).strip()],
            "critical_keywords": [str(item).strip() for item in rules.get("critical_keywords", []) if str(item).strip()],
        }

        if not step_name:
            errors.append(f"step #{index}: name is required")
        if not command:
            errors.append(f"step #{index}: command is required")
        else:
            try:
                _validate_command_template(command)
            except ValueError as exc:
                errors.append(f"step #{index}: invalid command: {exc}")
        if timeout_sec <= 0:
            errors.append(f"step #{index}: timeout_sec must be > 0")
        if normalized_rules["nonzero_exit_status"] not in {"ERROR", "WARNING", "CRITICAL"}:
            errors.append(f"step #{index}: nonzero_exit_status must be ERROR, WARNING, or CRITICAL")

        normalized_steps.append({
            "id": step.get("id"),
            "name": step_name,
            "command": command,
            "timeout_sec": timeout_sec,
            "run_on_each_host": run_on_each_host,
            "rules": normalized_rules,
        })

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "name": name,
        "system_name": system_name,
        "template_key": template_key,
        "enabled": enabled,
        "timezone": timezone,
        "daily_time": daily_time,
        "recipient_emails": recipient_emails,
        "hosts": normalized_hosts,
        "steps": normalized_steps,
    }


class DynamicModuleStore:
    def __init__(self, db_path: str | None = None):
        self.result_store = ResultStore(db_path=db_path)
        self.db_path = self.result_store.db_path
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS dynamic_modules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    system_name TEXT NOT NULL,
                    template_key TEXT NOT NULL DEFAULT 'custom',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    timezone TEXT NOT NULL,
                    daily_time TEXT NOT NULL,
                    recipient_emails TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS dynamic_module_hosts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 22,
                    username TEXT NOT NULL,
                    auth_type TEXT NOT NULL DEFAULT 'password',
                    encrypted_password TEXT DEFAULT '',
                    key_path TEXT DEFAULT '',
                    variables_json TEXT NOT NULL DEFAULT '{}',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (module_id) REFERENCES dynamic_modules(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS dynamic_module_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    command TEXT NOT NULL,
                    timeout_sec INTEGER NOT NULL DEFAULT 60,
                    run_on_each_host INTEGER NOT NULL DEFAULT 1,
                    rules_json TEXT NOT NULL DEFAULT '{}',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (module_id) REFERENCES dynamic_modules(id) ON DELETE CASCADE
                );
            """)
            self._ensure_column(conn, "dynamic_modules", "template_key", "TEXT NOT NULL DEFAULT 'custom'")
            self._ensure_column(conn, "dynamic_module_hosts", "variables_json", "TEXT NOT NULL DEFAULT '{}'")

    def _ensure_column(self, conn, table: str, column: str, definition: str):
        columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def list_modules(self, created_by: Optional[str] = None) -> list[DynamicModule]:
        """List modules. If created_by is given, only return that user's modules."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            if created_by is not None:
                rows = conn.execute("""
                    SELECT * FROM dynamic_modules
                    WHERE created_by = ? COLLATE NOCASE
                    ORDER BY name COLLATE NOCASE
                """, (created_by,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM dynamic_modules
                    ORDER BY name COLLATE NOCASE
                """).fetchall()
        return [self.get_module(row["id"]) for row in rows]

    def list_enabled_modules(self) -> list[DynamicModule]:
        return [module for module in self.list_modules() if module.enabled]

    def get_module(self, module_id: int) -> DynamicModule:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            module_row = conn.execute(
                "SELECT * FROM dynamic_modules WHERE id = ?",
                (module_id,),
            ).fetchone()
            if not module_row:
                raise KeyError(f"Dynamic module {module_id} not found")
            host_rows = conn.execute("""
                SELECT * FROM dynamic_module_hosts
                WHERE module_id = ?
                ORDER BY sort_order, id
            """, (module_id,)).fetchall()
            step_rows = conn.execute("""
                SELECT * FROM dynamic_module_steps
                WHERE module_id = ?
                ORDER BY sort_order, id
            """, (module_id,)).fetchall()
        return self._hydrate_module(module_row, host_rows, step_rows)

    def _hydrate_module(self, module_row, host_rows, step_rows) -> DynamicModule:
        return DynamicModule(
            id=module_row["id"],
            name=module_row["name"],
            system_name=module_row["system_name"],
            template_key=module_row["template_key"] or "custom",
            enabled=bool(module_row["enabled"]),
            timezone=module_row["timezone"],
            daily_time=module_row["daily_time"],
            recipient_emails=json.loads(module_row["recipient_emails"] or "[]"),
            created_by=module_row["created_by"] if "created_by" in module_row.keys() else "admin",
            hosts=[
                DynamicModuleHost(
                    id=row["id"],
                    name=row["name"],
                    ip=row["ip"],
                    port=row["port"],
                    username=row["username"],
                    auth_type=row["auth_type"],
                    encrypted_password=row["encrypted_password"] or "",
                    key_path=row["key_path"] or "",
                    variables=json.loads(row["variables_json"] or "{}"),
                )
                for row in host_rows
            ],
            steps=[
                DynamicModuleStep(
                    id=row["id"],
                    name=row["name"],
                    command=row["command"],
                    timeout_sec=row["timeout_sec"],
                    run_on_each_host=bool(row["run_on_each_host"]),
                    rules=json.loads(row["rules_json"] or "{}"),
                )
                for row in step_rows
            ],
            created_at=module_row["created_at"],
            updated_at=module_row["updated_at"],
        )

    def create_module(self, payload: dict[str, Any], created_by: str = "admin") -> DynamicModule:
        normalized = validate_module_payload(payload)
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO dynamic_modules
                    (name, system_name, template_key, enabled, timezone, daily_time, recipient_emails, created_by)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                normalized["name"],
                normalized["system_name"],
                normalized["template_key"],
                int(normalized["enabled"]),
                normalized["timezone"],
                normalized["daily_time"],
                json.dumps(normalized["recipient_emails"], ensure_ascii=False),
                created_by,
            ))
            module_id = cursor.lastrowid
            self._replace_hosts(conn, module_id, normalized["hosts"])
            self._replace_steps(conn, module_id, normalized["steps"])
        return self.get_module(module_id)

    def update_module(self, module_id: int, payload: dict[str, Any]) -> DynamicModule:
        existing = self.get_module(module_id)
        normalized = validate_module_payload(payload, existing=existing)
        with self._conn() as conn:
            conn.execute("""
                UPDATE dynamic_modules
                SET name = ?, system_name = ?, template_key = ?, enabled = ?, timezone = ?, daily_time = ?,
                    recipient_emails = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                normalized["name"],
                normalized["system_name"],
                normalized["template_key"],
                int(normalized["enabled"]),
                normalized["timezone"],
                normalized["daily_time"],
                json.dumps(normalized["recipient_emails"], ensure_ascii=False),
                module_id,
            ))
            conn.execute("DELETE FROM dynamic_module_hosts WHERE module_id = ?", (module_id,))
            conn.execute("DELETE FROM dynamic_module_steps WHERE module_id = ?", (module_id,))
            self._replace_hosts(conn, module_id, normalized["hosts"])
            self._replace_steps(conn, module_id, normalized["steps"])
        return self.get_module(module_id)

    def delete_module(self, module_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM dynamic_module_hosts WHERE module_id = ?", (module_id,))
            conn.execute("DELETE FROM dynamic_module_steps WHERE module_id = ?", (module_id,))
            conn.execute("DELETE FROM dynamic_modules WHERE id = ?", (module_id,))

    def _replace_hosts(self, conn, module_id: int, hosts: list[dict[str, Any]]):
        for index, host in enumerate(hosts):
            conn.execute("""
                INSERT INTO dynamic_module_hosts
                    (module_id, name, ip, port, username, auth_type, encrypted_password, key_path, variables_json, sort_order)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                module_id,
                host["name"],
                host["ip"],
                host["port"],
                host["username"],
                host["auth_type"],
                host["encrypted_password"],
                host["key_path"],
                json.dumps(host["variables"], ensure_ascii=False),
                index,
            ))

    def _replace_steps(self, conn, module_id: int, steps: list[dict[str, Any]]):
        for index, step in enumerate(steps):
            conn.execute("""
                INSERT INTO dynamic_module_steps
                    (module_id, name, command, timeout_sec, run_on_each_host, rules_json, sort_order)
                VALUES (?,?,?,?,?,?,?)
            """, (
                module_id,
                step["name"],
                step["command"],
                step["timeout_sec"],
                int(step["run_on_each_host"]),
                json.dumps(step["rules"], ensure_ascii=False),
                index,
            ))

    def get_module_last_run(self, module_id: int) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT * FROM check_runs
                WHERE run_type = 'dynamic_module' AND module_id = ?
                ORDER BY started_at DESC
                LIMIT 1
            """, (module_id,)).fetchone()
        return dict(row) if row else None

    def get_module_runs(self, module_id: int, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM check_runs
                WHERE run_type = 'dynamic_module' AND module_id = ?
                ORDER BY started_at DESC
                LIMIT ?
            """, (module_id, limit)).fetchall()
        return [dict(row) for row in rows]


def _result_status(exit_success: bool, output: str, rules: dict[str, Any]) -> str:
    critical_keywords = [item.lower() for item in rules.get("critical_keywords", [])]
    warning_keywords = [item.lower() for item in rules.get("warning_keywords", [])]
    output_lower = output.lower()
    if any(keyword in output_lower for keyword in critical_keywords):
        return "CRITICAL"
    if any(keyword in output_lower for keyword in warning_keywords):
        return "WARNING"
    if not exit_success:
        return str(rules.get("nonzero_exit_status", "ERROR")).upper()
    return "OK"


def _make_result(system: str, host: str, check_name: str, status: str, message: str, raw_output: str = "", details: Optional[dict[str, Any]] = None) -> CheckResult:
    return CheckResult(
        system=system,
        host=host,
        check_name=check_name,
        status=status,
        message=message,
        raw_output=raw_output,
        details=details or {},
    )


def _sanitize_value(value: str) -> str:
    """Validate a template value and return a normalized safe string."""
    if value is None:
        return ""

    sanitized = str(value).strip()
    if not sanitized:
        return ""

    for char in sanitized:
        if char in DISALLOWED_VALUE_CHARS:
            raise ValueError(f"contains disallowed character {char!r}")
        category = unicodedata.category(char)
        if char.isalnum() or category.startswith("M") or char in SAFE_VALUE_PUNCTUATION:
            continue
        raise ValueError(
            f"contains unsupported character {char!r}; only letters, digits, spaces, and . _ - / @ : + = , are allowed"
        )

    return sanitized


def _validate_command_template(command: str) -> None:
    """Validate command template for dangerous patterns."""

    dangerous_patterns = [
        (r"\$\(", "command substitution '$(' is not allowed"),
        (r"`", "backtick command substitution is not allowed"),
        (r"[\r\n\x00]", "control characters are not allowed"),
        (r"(?<!\S)sudo(?=\s|$)", "sudo is not allowed in dynamic module commands"),
        (r"(?<!\S)su(?=\s|$)", "su is not allowed in dynamic module commands"),
        (r"(?<!\S)rm\s+-rf\s+/(?:\s|$)", "destructive root deletion is not allowed"),
        (r">\s*/dev/(?!null\b)", "redirecting output to device files is not allowed"),
    ]

    for pattern, message in dangerous_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            raise ValueError(message)


def _validate_and_sanitize_context(context: dict) -> dict:
    """Validate and sanitize all context variables."""
    sanitized_context = {}
    
    for key, value in context.items():
        if value is None:
            sanitized_context[key] = ""
        else:
            try:
                sanitized_context[key] = _sanitize_value(str(value))
            except ValueError as exc:
                raise ValueError(f"Invalid value for '{key}': {exc}") from exc
    
    return sanitized_context


def _render_command(command: str, module: DynamicModule, host: DynamicModuleHost) -> str:
    """Render command template with validated and sanitized variables."""
    # Validate the command template first
    _validate_command_template(command)
    
    # Build context with built-in variables
    context = {
        "host_name": host.name,
        "host_ip": host.ip,
        "module_name": module.name,
        "system_name": module.system_name,
    }
    
    # Add host variables (convert all to strings)
    context.update({str(key): str(value) for key, value in host.variables.items()})
    
    # Validate and sanitize all context values
    sanitized_context = _validate_and_sanitize_context(context)
    
    template_vars = set(TEMPLATE_VAR_RE.findall(command))
    missing_vars = sorted(var for var in template_vars if var not in sanitized_context)
    if missing_vars:
        raise ValueError(f"Missing template variable(s): {', '.join(missing_vars)}")

    def replace_var(match: re.Match[str]) -> str:
        return sanitized_context[match.group(1)]

    rendered = TEMPLATE_VAR_RE.sub(replace_var, command)
    
    # Final validation of the rendered command
    _validate_command_template(rendered)
    
    return rendered


def _ssh_client_for_host(host: DynamicModuleHost) -> "SSHClient":
    from src.ssh_manager import SSHClient, SSHConfig

    password = decrypt_secret(host.encrypted_password) if host.auth_type == "password" else None
    cfg = SSHConfig(
        host=host.ip,
        port=host.port,
        username=host.username,
        password=password,
        key_path=host.key_path or None,
        timeout=get("general.ssh_timeout", 30),
    )
    client = SSHClient(cfg)
    if not client.connect():
        raise ConnectionError(f"Cannot SSH to {host.ip}")
    return client


def run_module_checks(module: DynamicModule) -> list[CheckResult]:
    results: list[CheckResult] = []
    hosts = module.hosts
    client_cache: dict[int, Any] = {}
    connection_errors: dict[int, str] = {}

    try:
        for step in module.steps:
            target_hosts = hosts if step.run_on_each_host else hosts[:1]
            for host in target_hosts:
                host_key = host.id if host.id is not None else id(host)
                display_host = host.name or host.ip

                if host_key in connection_errors:
                    results.append(_make_result(
                        module.system_name,
                        display_host,
                        step.name,
                        "ERROR",
                        connection_errors[host_key],
                        details={"command": step.command, "variables": host.variables},
                    ))
                    continue

                client = client_cache.get(host_key)
                if client is None:
                    try:
                        client = _ssh_client_for_host(host)
                        client_cache[host_key] = client
                    except ConnectionError as exc:
                        connection_errors[host_key] = str(exc)
                        results.append(_make_result(
                            module.system_name,
                            display_host,
                            step.name,
                            "ERROR",
                            str(exc),
                            details={"command": step.command, "variables": host.variables},
                        ))
                        continue

                try:
                    rendered_command = _render_command(step.command, module, host)
                    command_result = client.run(rendered_command, timeout=step.timeout_sec)
                    raw_output = "\n".join(part for part in [command_result.stdout, command_result.stderr] if part)
                    status = _result_status(command_result.success, raw_output, step.rules)
                    if status == "OK":
                        message = "Command completed successfully"
                    elif command_result.success:
                        message = f"Matched output rule: {status}"
                    else:
                        message = f"Command failed with exit code {command_result.exit_code}"
                    results.append(_make_result(
                        module.system_name,
                        display_host,
                        step.name,
                        status,
                        message,
                        raw_output=raw_output,
                        details={
                            "command": rendered_command,
                            "command_template": step.command,
                            "exit_code": command_result.exit_code,
                            "run_on_each_host": step.run_on_each_host,
                            "variables": host.variables,
                        },
                    ))
                except Exception as exc:
                    logger.exception("Dynamic module [%s] step [%s] failed on host %s", module.name, step.name, display_host)
                    results.append(_make_result(
                        module.system_name,
                        display_host,
                        step.name,
                        "ERROR",
                        str(exc),
                        details={"command": step.command, "variables": host.variables},
                    ))
    finally:
        for client in client_cache.values():
            try:
                client.close()
            except Exception:
                pass

    return results
