# User-Defined Check Modules

## Summary
Add a new dynamic module system that lets users create, edit, enable, schedule, run, and notify check modules from the dashboard without writing Python. Existing built-in Oracle/MongoDB/Hadoop/Application checks remain unchanged and continue to run through the current hardcoded path. New user-defined modules are SSH-based in v1, execute their own ordered command steps, and create independent run history and email reports per module execution.

## Key Changes
- Add a persistent module registry in SQLite for:
  - module metadata: `name`, `system_name`, `enabled`, `timezone`, `daily_time`, `recipient_emails`
  - target hosts: `name`, `ip`, `port`, `username`, `auth_type`, `encrypted_password`, `key_path`
  - step definitions: ordered list of SSH command steps with `name`, `command`, `timeout_sec`, `run_on_each_host`, and rule config
  - rule config: exit-code handling plus optional stdout/stderr keyword match rules that map to `OK`, `WARNING`, `CRITICAL`
- Add encryption for stored SSH passwords:
  - use an app-level encryption secret in config
  - store ciphertext only in SQLite
  - decrypt only at execution time
- Introduce a dynamic module runner:
  - load enabled user-defined modules from SQLite
  - for each module run, connect to its declared hosts via existing SSH manager primitives
  - execute steps in declared order
  - evaluate each step result using exit code first, then keyword rules
  - emit normal `CheckResult` objects so reporting/dashboard stay compatible
- Extend orchestration model:
  - keep current `run_daily_check()` for built-in consolidated runs
  - add a separate `run_dynamic_module(module_id)` path for one module at a time
  - each dynamic module execution creates its own `run_id`, `RunSummary`, HTML report, and email send
  - tag runs with source metadata so dashboard/API can distinguish built-in aggregate runs from dynamic module runs
- Extend scheduler:
  - keep the existing single built-in daily job
  - register one APScheduler job per enabled dynamic module using daily `HH:MM`
  - support enable/disable and reschedule on module create/update/delete
  - keep overlap protection per module so a module cannot start again while its previous run is active
- Extend alert/report behavior:
  - keep current SMTP settings global
  - recipients become per-module for dynamic modules
  - send one report email per module execution to that module’s recipient list
  - built-in runs keep using the existing global recipient list unless explicitly refactored later
- Add dashboard management UI and APIs:
  - list modules with status, next run time, enabled flag, and last run
  - create/edit/delete module
  - manage hosts and ordered steps
  - trigger a module manually
  - view module-specific run history and run details
  - validate schedule format, recipient emails, required host fields, and step definitions before save

## Public Interfaces / Data Model
- Add config entries:
  - `security.encryption_key` for credential encryption/decryption
  - keep existing `notifications.email` as SMTP transport config only
- Add new tables, or equivalent normalized schema:
  - `dynamic_modules`
  - `dynamic_module_hosts`
  - `dynamic_module_steps`
  - optionally `dynamic_module_step_rules` if rules are normalized instead of JSON
- Extend run storage shape:
  - add fields to run records for `run_type` (`builtin` or `dynamic_module`) and `module_id` / `module_name` when applicable
- Add dashboard/API endpoints for dynamic modules:
  - list/create/update/delete modules
  - trigger module now
  - fetch module run history and details

## Test Plan
- Create a dynamic module with one host and one simple command step; verify save, scheduler registration, manual trigger, run persistence, and report generation.
- Create a module with multiple ordered steps and verify `CheckResult` ordering and summary counts.
- Verify rule evaluation:
  - exit code `0` with no matches -> `OK`
  - exit code nonzero -> `ERROR` or configured failure status
  - stdout/stderr warning keyword match -> `WARNING`
  - stdout/stderr critical keyword match -> `CRITICAL`
  - critical rule wins over warning when both match
- Verify host execution behavior:
  - valid SSH credentials -> step results recorded
  - invalid credentials -> connection error result per host
  - timeout -> error result with timeout message
- Verify scheduling behavior:
  - enabled module schedules at configured `HH:MM`
  - disabled module does not schedule
  - edited time reschedules correctly
  - concurrent trigger for the same module is skipped while already running
- Verify notification behavior:
  - module report goes only to that module’s recipients
  - built-in run still uses existing global recipients
- Verify dashboard/API:
  - CRUD validation failures return useful errors
  - run history shows built-in and dynamic runs distinctly
  - manual trigger endpoint starts only the requested module

## Assumptions and Defaults
- v1 dynamic modules support SSH command steps only; no HTTP checks or Python plugin logic.
- Built-in hardcoded modules remain active and unchanged.
- Dynamic modules run independently, not batched together.
- Schedule format for dynamic modules is daily local `HH:MM`, not cron.
- Password storage is encrypted in SQLite with an app-managed key; no external secret manager is introduced in v1.
- Result evaluation uses exit code plus keyword rules; richer expressions are out of scope for v1.

