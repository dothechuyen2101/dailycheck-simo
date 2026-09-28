# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

SIMO Daily Check automates the daily health check of Oracle RAC, MongoDB, Hadoop and application servers over SSH. It also runs user-defined "dynamic modules" (SSH command steps configured through the dashboard). Code comments, log messages, UI text and most docs (README.md, ARCHITECTURE.md, USER_MANUAL.md) are in Vietnamese; keep new user-facing strings consistent with that. AGENTS.md has a condensed English summary of the system.

## Commands

```bash
pip install -r requirements.txt       # Python 3.9+ (Dockerfile uses 3.11)

python main.py                        # scheduler + dashboard (default)
python main.py --run-now              # one built-in check run, then exit (exit code 0 only if overall OK)
python main.py --dashboard            # Flask dashboard only
python main.py --scheduler            # scheduler only
python main.py --config path.yaml --log-level DEBUG

docker-compose up --build

# Tests (pytest is not in requirements.txt; install it separately)
python -m pytest tests/
python -m pytest tests/test_dynamic_modules.py::DynamicModuleTests::test_validate_encrypts_password_and_keeps_rules
python tests/test_validation_simple.py   # standalone script, no pytest needed
```

There is no linter or formatter configured.

`config/config.yaml` is gitignored and not in the repo. Create it before running anything (README.md §4 shows the structure). Tests don't need it: they write a temporary YAML with `general.*` and `security.encryption_key` and call `load_config(path)` in `setUp`.

The root-level `verify_validation.py` and `test_edge_cases.py` are ad-hoc scripts, not part of the test suite. `verify_validation.py` is out of date: it expects `_sanitize_value` to strip unsafe characters, but the function now raises `ValueError`. `extract.py` only dumps the `.docx` specs into `plan.txt`.

## Architecture

**Config is global module state.** `src/config_manager.load_config()` fills a module-level dict, and everything reads it through `get("dotted.key", default)`. Most modules call `get()` at call time, not import time. However, `dashboard/app.py` calls `load_config()` with no arguments at import time and builds its `ResultStore`/`DynamicModuleStore` there. Importing the dashboard therefore reloads the default `config/config.yaml`, which overrides any `--config` path passed to `main.py`.

**Two run types share one pipeline** (`src/orchestrator.py`):
- `run_daily_check()` runs the four built-in modules in parallel with a `ThreadPoolExecutor`. Each module's `run_all()` returns `List[CheckResult]`.
- `run_dynamic_module(module_id)` loads a module from SQLite and runs `dynamic_modules.run_module_checks()`.
- Both end in `_finalize_run()`. It persists the results and the `RunSummary`, computes the overall status (any CRITICAL/ERROR → CRITICAL, else any WARNING → WARNING, else OK), renders the HTML report, emails it, and writes `data/reports/report_<run_id>.html`. A run row is saved at start and updated at the end, so an interrupted run leaves a partial row behind.

**Built-in check modules** (`src/checks/*_checks.py`) all follow the same pattern:
- A module-level `SYSTEM` name and `_ok/_warn/_crit/_err` helpers that build `CheckResult`.
- One `check_*` function per numbered step (step names like `"Bước 2 – Phân vùng đĩa"`).
- A `run_all()` that iterates the hosts in the config section, opens an SSH connection with `ssh_manager.ssh_connect(host_cfg)`, and runs the steps.
- Every check parses shell output (`df`, `crs_stat`, `systemctl`, `docker ps`, etc.). Oracle SQL checks pipe SQL into `sqlplus -S / as sysdba` over SSH; `cx_Oracle`/`oracledb` isn't used. Thresholds come from the `thresholds.*` config keys, with hardcoded defaults.

**Persistence** (`src/logger.py`, which also holds `CheckResult`, `RunSummary`, `ResultStore` and `setup_logging`):
- SQLite at `data/dailycheck.db`, with tables `check_runs`, `check_results` and `alert_history`.
- There is no migration framework. Schema changes go in `_init_db()` using `CREATE TABLE IF NOT EXISTS` plus `_ensure_column()`, which runs `ALTER TABLE ADD COLUMN` if the column is missing. `DynamicModuleStore` in `src/dynamic_modules.py` uses the same pattern for `dynamic_modules`, `dynamic_module_hosts` and `dynamic_module_steps`.

**Dynamic modules** (`src/dynamic_modules.py`):
- `validate_module_payload()` normalizes dashboard/API input and encrypts host passwords with `src/security.py`. That module derives a Fernet key from the SHA-256 of `security.encryption_key`, so changing that key makes stored passwords undecryptable.
- Step commands are templates using `{{var}}`. The available variables are `host_name`, `host_ip`, `module_name`, `system_name`, plus each host's `variables`.
- `_render_command()` has these security layers:
  - It rejects dangerous templates: `$(`, backticks, `sudo`/`su`, `rm -rf /`, and redirects to `/dev/*`.
  - It rejects variable values that contain disallowed characters. Only letters, digits, spaces and `. _ - / @ : + = ,` are allowed.
  - It re-validates the rendered command.
  - `tests/test_command_validation.py` covers these rules; keep it in sync when changing them.
- Step status comes from `_result_status()`. Critical keywords are checked first, then warning keywords, then a non-zero exit code (which maps to `rules.nonzero_exit_status`, default ERROR).
- SSH clients are cached per host within a run. A connection failure marks every later step on that host as ERROR, with no retry.

**Scheduler** (`src/scheduler.py`):
- A `BackgroundScheduler` runs the job `daily_check` at `general.check_time`, plus one job per enabled dynamic module (`dynamic_module_<id>`), each with its own time and timezone.
- Overlap protection is an in-process flag or lock per job. A trigger that fires while the same job is still running is skipped.
- After any create, update or delete through the API, the dashboard must call `resync_dynamic_jobs()`.
- `general.prep_time` is mentioned in the docs but is not implemented.

**Alerts** (`src/alert_manager.py`): per-result real-time alerts are disabled (`alert()` only logs). Notification is one consolidated HTML report per run. `send_report()` sends it only by email, and only if `notifications.email.enabled` is set. `_send_webhook()` exists but nothing calls it for reports. Dynamic modules send to their own `recipient_emails`.

**Dashboard** (`dashboard/app.py`):
- A single-file Flask app with Jinja templates in `dashboard/templates/` and vendored Bootstrap in `static/`.
- `/report/<run_id>` serves the saved HTML report; `/report/<run_id>/download` generates a PDF on the fly with `src/pdf_report_generator.py` (reportlab). `_register_font()` looks for DejaVu, Noto, Arial or Liberation fonts under Linux `/usr/share/fonts` paths so Vietnamese text renders; the Dockerfile installs `fonts-dejavu-core` for this..
- Manual triggers (`/api/trigger`, `/api/modules/<id>/trigger`) call the scheduler's `trigger_*_now()` functions, which run in a daemon thread.
