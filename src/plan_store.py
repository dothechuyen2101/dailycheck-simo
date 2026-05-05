"""
Plan Store – SQLite CRUD cho systems, hosts, plans, plan_steps và run snapshots.
Dùng cho Dashboard nhập động cấu hình Daily Check.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.config_manager import get


def _now() -> str:
    return datetime.now().isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _json_loads(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


class PlanStore:
    """Lưu trữ cấu hình hệ thống, host và plan check do người dùng nhập."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            data_dir = Path(get("general.data_dir", "data"))
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "dailycheck.db")
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS systems (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT NOT NULL,
                    system_type   TEXT NOT NULL,
                    description   TEXT,
                    enabled       INTEGER DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS system_hosts (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    system_id     INTEGER NOT NULL,
                    name          TEXT NOT NULL,
                    ip            TEXT NOT NULL,
                    username      TEXT NOT NULL,
                    password      TEXT,
                    key_path      TEXT,
                    ssh_port      INTEGER DEFAULT 22,
                    role          TEXT,
                    extra_json    TEXT,
                    enabled       INTEGER DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    FOREIGN KEY (system_id) REFERENCES systems(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS plans (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT NOT NULL,
                    description   TEXT,
                    is_default    INTEGER DEFAULT 0,
                    enabled       INTEGER DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS plan_steps (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id         INTEGER NOT NULL,
                    system_id       INTEGER NOT NULL,
                    step_id         TEXT NOT NULL,
                    step_name       TEXT,
                    step_order      INTEGER DEFAULT 0,
                    enabled         INTEGER DEFAULT 1,
                    host_scope_json TEXT,
                    params_json     TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE,
                    FOREIGN KEY (system_id) REFERENCES systems(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_plan_snapshots (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id        TEXT NOT NULL,
                    plan_id       INTEGER,
                    snapshot_json TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_hosts_system ON system_hosts(system_id);
                CREATE INDEX IF NOT EXISTS idx_steps_plan ON plan_steps(plan_id);
                CREATE INDEX IF NOT EXISTS idx_steps_system ON plan_steps(system_id);
                CREATE INDEX IF NOT EXISTS idx_snapshot_run ON run_plan_snapshots(run_id);
                """
            )

    # ── Systems ───────────────────────────────────────────────────────────
    def list_systems(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM systems ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_system(self, system_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM systems WHERE id=?", (system_id,)).fetchone()
        return dict(row) if row else None

    def create_system(self, data: dict) -> int:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO systems (name, system_type, description, enabled, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    data["name"],
                    data["system_type"],
                    data.get("description", ""),
                    1 if data.get("enabled", True) else 0,
                    now,
                    now,
                ),
            )
            return cur.lastrowid

    def update_system(self, system_id: int, data: dict) -> bool:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE systems SET name=?, system_type=?, description=?, enabled=?, updated_at=?
                WHERE id=?
                """,
                (
                    data["name"],
                    data["system_type"],
                    data.get("description", ""),
                    1 if data.get("enabled", True) else 0,
                    now,
                    system_id,
                ),
            )
            return cur.rowcount > 0

    def delete_system(self, system_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM systems WHERE id=?", (system_id,))
            return cur.rowcount > 0

    # ── Hosts ─────────────────────────────────────────────────────────────
    def list_hosts(self, system_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM system_hosts WHERE system_id=? ORDER BY id",
                (system_id,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["extra"] = _json_loads(d.get("extra_json"), {})
            result.append(d)
        return result

    def get_host(self, host_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM system_hosts WHERE id=?", (host_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["extra"] = _json_loads(d.get("extra_json"), {})
        return d

    def create_host(self, system_id: int, data: dict) -> int:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO system_hosts
                    (system_id, name, ip, username, password, key_path, ssh_port,
                     role, extra_json, enabled, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    system_id,
                    data["name"],
                    data["ip"],
                    data["username"],
                    data.get("password"),
                    data.get("key_path"),
                    int(data.get("ssh_port", 22)),
                    data.get("role"),
                    _json_dumps(data.get("extra", {})),
                    1 if data.get("enabled", True) else 0,
                    now,
                    now,
                ),
            )
            return cur.lastrowid

    def update_host(self, host_id: int, data: dict) -> bool:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE system_hosts SET
                    name=?, ip=?, username=?, password=?, key_path=?, ssh_port=?,
                    role=?, extra_json=?, enabled=?, updated_at=?
                WHERE id=?
                """,
                (
                    data["name"],
                    data["ip"],
                    data["username"],
                    data.get("password"),
                    data.get("key_path"),
                    int(data.get("ssh_port", 22)),
                    data.get("role"),
                    _json_dumps(data.get("extra", {})),
                    1 if data.get("enabled", True) else 0,
                    now,
                    host_id,
                ),
            )
            return cur.rowcount > 0

    def delete_host(self, host_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM system_hosts WHERE id=?", (host_id,))
            return cur.rowcount > 0

    # ── Plans ─────────────────────────────────────────────────────────────
    def list_plans(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM plans ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_plan(self, plan_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        return dict(row) if row else None

    def get_default_plan(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM plans WHERE is_default=1 AND enabled=1 ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT * FROM plans WHERE enabled=1 ORDER BY id LIMIT 1"
                ).fetchone()
        return dict(row) if row else None

    def create_plan(self, data: dict) -> int:
        now = _now()
        is_default = 1 if data.get("is_default") else 0
        with self._conn() as conn:
            if is_default:
                conn.execute("UPDATE plans SET is_default=0")
            cur = conn.execute(
                """
                INSERT INTO plans (name, description, is_default, enabled, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    data["name"],
                    data.get("description", ""),
                    is_default,
                    1 if data.get("enabled", True) else 0,
                    now,
                    now,
                ),
            )
            return cur.lastrowid

    def update_plan(self, plan_id: int, data: dict) -> bool:
        now = _now()
        is_default = 1 if data.get("is_default") else 0
        with self._conn() as conn:
            if is_default:
                conn.execute("UPDATE plans SET is_default=0")
            cur = conn.execute(
                """
                UPDATE plans SET name=?, description=?, is_default=?, enabled=?, updated_at=?
                WHERE id=?
                """,
                (
                    data["name"],
                    data.get("description", ""),
                    is_default,
                    1 if data.get("enabled", True) else 0,
                    now,
                    plan_id,
                ),
            )
            return cur.rowcount > 0

    def delete_plan(self, plan_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM plans WHERE id=?", (plan_id,))
            return cur.rowcount > 0

    # ── Plan steps ────────────────────────────────────────────────────────
    def list_plan_steps(self, plan_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM plan_steps WHERE plan_id=? ORDER BY step_order, id",
                (plan_id,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["host_scope"] = _json_loads(d.get("host_scope_json"), {"mode": "all"})
            d["params"] = _json_loads(d.get("params_json"), {})
            result.append(d)
        return result

    def get_plan_step(self, step_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM plan_steps WHERE id=?", (step_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["host_scope"] = _json_loads(d.get("host_scope_json"), {"mode": "all"})
        d["params"] = _json_loads(d.get("params_json"), {})
        return d

    def create_plan_step(self, plan_id: int, data: dict) -> int:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO plan_steps
                    (plan_id, system_id, step_id, step_name, step_order, enabled,
                     host_scope_json, params_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    plan_id,
                    int(data["system_id"]),
                    data["step_id"],
                    data.get("step_name"),
                    int(data.get("step_order", 0)),
                    1 if data.get("enabled", True) else 0,
                    _json_dumps(data.get("host_scope", {"mode": "all"})),
                    _json_dumps(data.get("params", {})),
                    now,
                    now,
                ),
            )
            return cur.lastrowid

    def update_plan_step(self, step_id: int, data: dict) -> bool:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE plan_steps SET
                    system_id=?, step_id=?, step_name=?, step_order=?, enabled=?,
                    host_scope_json=?, params_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    int(data["system_id"]),
                    data["step_id"],
                    data.get("step_name"),
                    int(data.get("step_order", 0)),
                    1 if data.get("enabled", True) else 0,
                    _json_dumps(data.get("host_scope", {"mode": "all"})),
                    _json_dumps(data.get("params", {})),
                    now,
                    step_id,
                ),
            )
            return cur.rowcount > 0

    def delete_plan_step(self, step_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM plan_steps WHERE id=?", (step_id,))
            return cur.rowcount > 0

    # ── Snapshots ─────────────────────────────────────────────────────────
    def save_run_snapshot(self, run_id: str, plan_id: Optional[int], snapshot: dict):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO run_plan_snapshots (run_id, plan_id, snapshot_json, created_at)
                VALUES (?,?,?,?)
                """,
                (run_id, plan_id, _json_dumps(snapshot), _now()),
            )

    def get_run_snapshot(self, run_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM run_plan_snapshots WHERE run_id=? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["snapshot"] = _json_loads(d.get("snapshot_json"), {})
        return d
