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

## Architecture
- 4 check modules run in **parallel** via `ThreadPoolExecutor`: Oracle, MongoDB, Hadoop, Application
- `max_workers` defaults to 10, controlled by `general.max_workers`
- Each module returns `List[CheckResult]`, aggregated in `orchestrator.py`
- Overall status: CRITICAL if any CRITICAL/ERROR, else WARNING if any WARNING, else OK

## Scheduler
- APScheduler `CronTrigger`, runs mon-fri at `general.check_time` (default "08:00")
- Prep time `general.prep_time` ("07:55") is for SSH connection warming only
- Misfire grace time: 300s; skip new trigger if previous run still active

## Dashboard
- Flask app on `dashboard.host:port` (default `0.0.0.0:5000`)
- Healthcheck: `GET /api/status`
- Uses secret key from `dashboard.secret_key`

## Check Modules
- `src/checks/oracle_checks.py` – 11 steps via SSH/sqlplus + optional cx_Oracle
- `src/checks/mongo_checks.py` – 3 steps via SSH
- `src/checks/hadoop_checks.py` – 3 steps via SSH + `hdfs dfsadmin`
- `src/checks/app_checks.py` – 3 steps × N hosts, supports Docker replicas

## Data & Storage
- SQLite DB: `data/dailycheck.db`
- HTML reports saved: `data/reports/report_<run_id>.html`
- Logs: `logs/` directory

## Security
- SSH key auth preferred; key path via `key_path` in host config
- Passwords in config are placeholders (`CHANGE_ME`) – must be replaced for production
- `.gitignore` excludes `config/config.yaml` and `data/`

## Docker
```bash
docker-compose up --build
# Runs: python main.py (scheduler + dashboard)
```
- SSH keys mounted read-only: `${HOME}/.ssh:/root/.ssh:ro`
- Healthcheck polls `/api/status` every 30s

## Testing
- `tests/` directory exists but is **empty** – no test suite yet
- Manual verification: `python main.py --run-now`

## Key Thresholds (config/config.yaml)
- Disk: `%used >= 90%` → WARNING
- ASM: `%used >= 90%` → WARNING
- Voting disk: must have exactly 3 ONLINE
- Tablespace free: SYSTEM/SYSAUX >= 3%, UNDOTBS >= 400MB total, others >= 15%