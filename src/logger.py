"""
Logger & Result Store – Ghi log có cấu trúc và lưu kết quả vào SQLite.
"""
import logging
import sqlite3
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

from src.config_manager import get
from src import timeutil


# ── Logging setup ────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> logging.Logger:
    log_dir = Path(get("general.log_dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"dailycheck_{timeutil.now():%Y%m%d}.log"

    fmt = "%(asctime)s [%(levelname)s] %(name)s – %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger("dailycheck")


logger = logging.getLogger("dailycheck")


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    """Kết quả của một bước kiểm tra."""
    system: str               # oracle / mongodb / hadoop / app_name
    host: str                 # IP hoặc hostname
    check_name: str           # Tên bước kiểm tra
    status: str               # OK / WARNING / CRITICAL / ERROR
    message: str              # Mô tả ngắn
    raw_output: str = ""      # Output thô từ lệnh
    details: dict = field(default_factory=dict)
    checked_at: str = field(default_factory=lambda: timeutil.now().isoformat())


@dataclass
class RunSummary:
    """Tổng hợp một lần chạy Daily Check."""
    run_id: str
    started_at: str
    run_type: str = "builtin"
    module_id: Optional[int] = None
    module_name: str = ""
    finished_at: str = ""
    overall_status: str = "OK"   # OK / WARNING / CRITICAL
    total_checks: int = 0
    ok_count: int = 0
    warning_count: int = 0
    critical_count: int = 0
    error_count: int = 0
    results: list = field(default_factory=list)


# ── SQLite Result Store ───────────────────────────────────────────────────────

class ResultStore:
    """Lưu trữ kết quả kiểm tra vào SQLite."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            data_dir = Path(get("general.data_dir", "data"))
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "dailycheck.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS check_runs (
                    run_id      TEXT PRIMARY KEY,
                    started_at  TEXT NOT NULL,
                    run_type    TEXT NOT NULL DEFAULT 'builtin',
                    module_id   INTEGER,
                    module_name TEXT DEFAULT '',
                    finished_at TEXT,
                    overall_status TEXT DEFAULT 'OK',
                    total_checks   INTEGER DEFAULT 0,
                    ok_count       INTEGER DEFAULT 0,
                    warning_count  INTEGER DEFAULT 0,
                    critical_count INTEGER DEFAULT 0,
                    error_count    INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS check_results (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      TEXT NOT NULL,
                    system      TEXT NOT NULL,
                    host        TEXT NOT NULL,
                    check_name  TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    message     TEXT,
                    raw_output  TEXT,
                    details     TEXT,
                    checked_at  TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES check_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS alert_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      TEXT NOT NULL,
                    system      TEXT NOT NULL,
                    host        TEXT NOT NULL,
                    check_name  TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    message     TEXT,
                    alerted_at  TEXT NOT NULL,
                    channel     TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_results_run
                    ON check_results(run_id);
                CREATE INDEX IF NOT EXISTS idx_runs_started
                    ON check_runs(started_at);
            """)
            self._ensure_column(conn, "check_runs", "run_type", "TEXT NOT NULL DEFAULT 'builtin'")
            self._ensure_column(conn, "check_runs", "module_id", "INTEGER")
            self._ensure_column(conn, "check_runs", "module_name", "TEXT DEFAULT ''")

    def _ensure_column(self, conn, table: str, column: str, definition: str):
        columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def save_run(self, summary: RunSummary):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO check_runs
                    (run_id, started_at, run_type, module_id, module_name, finished_at, overall_status,
                     total_checks, ok_count, warning_count, critical_count, error_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                summary.run_id, summary.started_at, summary.run_type,
                summary.module_id, summary.module_name, summary.finished_at,
                summary.overall_status, summary.total_checks,
                summary.ok_count, summary.warning_count,
                summary.critical_count, summary.error_count,
            ))

    def save_result(self, run_id: str, r: CheckResult):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO check_results
                    (run_id, system, host, check_name, status, message,
                     raw_output, details, checked_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                run_id, r.system, r.host, r.check_name, r.status,
                r.message, r.raw_output,
                json.dumps(r.details, ensure_ascii=False), r.checked_at,
            ))

    def save_alert(self, run_id: str, r: CheckResult, channel: str):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO alert_history
                    (run_id, system, host, check_name, status, message, alerted_at, channel)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                run_id, r.system, r.host, r.check_name,
                r.status, r.message, timeutil.now().isoformat(), channel,
            ))

    def get_recent_runs(
        self,
        limit: int = 30,
        run_type: str | None = None,
        module_id: int | None = None,
    ) -> list[dict]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM check_runs"
            conditions = []
            params = []
            if run_type:
                conditions.append("run_type = ?")
                params.append(run_type)
            if module_id is not None:
                conditions.append("module_id = ?")
                params.append(module_id)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def get_run_results(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM check_results
                WHERE run_id = ? ORDER BY checked_at
            """, (run_id,)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details") or "{}")
            except Exception:
                d["details"] = {}
            results.append(d)
        return results

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT * FROM check_runs
                WHERE run_id = ?
            """, (run_id,)).fetchone()
        return dict(row) if row else None

    def get_latest_run(self) -> Optional[dict]:
        runs = self.get_recent_runs(limit=1)
        return runs[0] if runs else None
