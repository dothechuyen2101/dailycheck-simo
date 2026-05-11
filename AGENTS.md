# SIMO Daily Check – Agent Notes

## Entry Point
```bash
python main.py                      # scheduler + dashboard (default)
python main.py --run-now            # run once immediately and exit
python main.py --dashboard          # dashboard only
python main.py --scheduler          # scheduler only
python main.py --config path.yaml   # custom config file
```

## Config
- Config file: `config/config.yaml`
- Loaded via `src.config_manager.load_config()` before any module imports
- Access values: `from src.config_manager import get; get("general.ssh_timeout")`
- Default paths expand `~` via `os.path.expanduser()`
- Dynamic module password encryption key: `security.encryption_key`

## Architecture
- 4 check modules run in **parallel** via `ThreadPoolExecutor`: Oracle, MongoDB, Hadoop, Application
- `max_workers` defaults to 10, controlled by `general.max_workers`
- Each module returns `List[CheckResult]`, aggregated in `orchestrator.py`
- Overall status: CRITICAL if any CRITICAL/ERROR, else WARNING if any WARNING, else OK
- Dynamic user-defined modules are stored in SQLite and run independently via `run_dynamic_module(module_id)`
- Dynamic module steps are SSH command based and evaluated by exit code plus keyword rules

## Scheduler
- APScheduler `CronTrigger`, runs every day at `general.check_time` (default "08:00")
- Misfire grace time: 300s; skip new trigger if previous run still active
- Each enabled dynamic module has its own daily APScheduler job and per-module overlap protection

## Dashboard
- Flask app on `dashboard.host:port` (default `0.0.0.0:5000`)
- Healthcheck: `GET /api/status`
- Uses secret key from `dashboard.secret_key`
- Dynamic module UI: `/modules`, `/modules/new`, `/modules/<id>/edit`
- Dynamic module API: `/api/modules`, `/api/modules/<id>`, `/api/modules/<id>/trigger`, `/api/modules/<id>/runs`
- Protected routes require login: `@login_required` decorator
- Admin routes require admin role: `@admin_required` decorator
- Login page: `/login` (GET/POST)
- Logout: `/logout`

## Authentication & RBAC
- User model stored in SQLite table `users`
- Passwords hashed with PBKDF2-HMAC-SHA256 (260,000 iterations) + random salt
- Two roles: `admin` (full access), `user` (own modules only)
- Session management via Flask session (user_id, username, role)
- Default admin created on first run if no users exist
- Config settings:
  - `auth.admin_username` (default: "admin")
  - `auth.admin_password` (default: "admin123" → **CHANGE in production**)
- User management API: `/api/users` (admin only)
- Module ownership tracked via `dynamic_modules.created_by` column
- Route protection:
  - `@login_required`: any authenticated user
  - `@admin_required`: admin role only, else abort(403)
  - Built-in modules: admin can trigger, users cannot
  - User modules: creator + admin can edit/delete/trigger

## Check Modules
- `src/checks/oracle_checks.py` – 11 steps via SSH/sqlplus + optional cx_Oracle
- `src/checks/mongo_checks.py` – 3 steps via SSH
- `src/checks/hadoop_checks.py` – 3 steps via SSH + `hdfs dfsadmin`
- `src/checks/app_checks.py` – 3 steps × N hosts, supports Docker replicas

## Data & Storage
- SQLite DB: `data/dailycheck.db`
- HTML reports saved: `data/reports/report_<run_id>.html`
- Logs: `logs/` directory
- Additional SQLite tables: `dynamic_modules`, `dynamic_module_hosts`, `dynamic_module_steps`
- `check_runs` now includes `run_type`, `module_id`, and `module_name`

## Security
- SSH key auth preferred; key path via `key_path` in host config
- Passwords in config are placeholders (`CHANGE_ME`) – must be replaced for production
- `.gitignore` excludes `config/config.yaml` and `data/`
- Dynamic module SSH passwords are stored encrypted in SQLite and decrypted only at runtime
- **User passwords**: PBKDF2-HMAC-SHA256 hashed + random salt (260k iterations), never stored plaintext
- **Default credentials**: MUST change `auth.admin_password` in config before production
- **Dashboard secret_key**: Must be set to random strong value in production (used for session encryption)
- **Module ownership**: Users can only edit/delete their own modules; admins can manage all

## Docker
```bash
docker-compose up --build
# Runs: python main.py (scheduler + dashboard)
```
- SSH keys mounted read-only: `${HOME}/.ssh:/root/.ssh:ro`
- Healthcheck polls `/api/status` every 30s

## Testing
- Unit tests exist for dynamic module validation, encryption, and status rule evaluation
- Manual verification: `python main.py --run-now`

## Key Thresholds (config/config.yaml)
- Disk: `%used >= 90%` → WARNING
- ASM: `%used >= 90%` → WARNING
- Voting disk: must have exactly 3 ONLINE
- Tablespace free: SYSTEM/SYSAUX >= 3%, UNDOTBS >= 400MB total, others >= 15%
