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
- Prep time `general.prep_time` ("07:55") is for SSH connection warming only
- Misfire grace time: 300s; skip new trigger if previous run still active
- Each enabled dynamic module has its own daily APScheduler job and per-module overlap protection

## Dashboard
- Flask app on `dashboard.host:port` (default `0.0.0.0:5000`)
- Healthcheck: `GET /api/status`
- Uses secret key from `dashboard.secret_key`
- Dynamic module UI: `/modules`, `/modules/new`, `/modules/<id>/edit`
- Dynamic module API: `/api/modules`, `/api/modules/<id>`, `/api/modules/<id>/trigger`, `/api/modules/<id>/runs`
- Built-in command editor: `/commands`; API `GET /api/builtin-commands`, `PUT|DELETE /api/builtin-commands/<step_key>`

## Check Modules
- `src/checks/oracle_checks.py` – 11 steps via SSH/sqlplus + optional cx_Oracle
- `src/checks/mongo_checks.py` – 3 steps via SSH
- `src/checks/hadoop_checks.py` – 3 steps via SSH + `hdfs dfsadmin`
- `src/checks/app_checks.py` – 3 steps × N hosts, supports Docker replicas
- Commands are not hardcoded in the check modules: defaults live in `src/builtin_commands.py` (`BUILTIN_STEPS`), dashboard overrides in SQLite; parsing stays in the check modules
- Oracle hosts are AIX, SSH as root: sqlplus runs via `su - {{oracle_user}} -c` with explicit ORACLE_HOME, grid tools via `{{grid_home}}/bin`, CRS via `crsctl stat res -t` (no `crs_stat` in 19c); grid/oracle home and user come from config or are auto-detected (`ps`, `/etc/oratab`)
- Timestamps: use `src.timeutil.now()` (follows `general.timezone`), not `datetime.now()`
- Applications support `service_type: systemd|docker|process` (systemctl, Docker Swarm service, or process command-line match), overridable per host; `process_pattern` resolves `active (exited)` units

## Data & Storage
- SQLite DB: `data/dailycheck.db`
- HTML reports saved: `data/reports/report_<run_id>.html`
- Logs: `logs/` directory
- Additional SQLite tables: `dynamic_modules`, `dynamic_module_hosts`, `dynamic_module_steps`, `builtin_step_overrides`
- `check_runs` now includes `run_type`, `module_id`, and `module_name`

## Security
- SSH key auth preferred; key path via `key_path` in host config
- Passwords in config are placeholders (`CHANGE_ME`) – must be replaced for production
- `.gitignore` excludes `config/config.yaml` and `data/`
- Dynamic module SSH passwords are stored encrypted in SQLite and decrypted only at runtime

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
