# SIMO Daily Check System – Kiến trúc & Luồng hoạt động

## Mục lục

1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Sơ đồ kiến trúc](#2-sơ-đồ-kiến-trúc)
3. [Cấu trúc thư mục](#3-cấu-trúc-thư-mục)
4. [Luồng hoạt động chính](#4-luồng-hoạt-động-chính)
5. [Chi tiết từng module](#5-chi-tiết-từng-module)
6. [Mô hình dữ liệu](#6-mô-hình-dữ-liệu)
7. [Cơ chế xử lý song song](#7-cơ-chế-xử-lý-song-song)
8. [Scheduler & Timing](#8-scheduler--timing)
9. [Dashboard & API](#9-dashboard--api)
10. [Alert & Notification](#10-alert--notification)
11. [Bảo mật](#11-bảo-mật)

---

## 1. Tổng quan hệ thống

SIMO Daily Check là hệ thống tự động kiểm tra tình trạng hàng ngày cho 4 nhóm hạ tầng và hỗ trợ thêm dynamic modules do người dùng khai báo:

| Hệ thống | Số node | Số bước kiểm tra | Tổng |
|----------|---------|------------------|------|
| **Oracle RAC** | 2+ | 11 bước/node | ~20 kết quả |
| **MongoDB** | 2+ | 3 bước/node | ~6 kết quả |
| **Hadoop** | 3+ | 3 bước/node | ~9 kết quả |
| **Application** | N hosts × app | 3-4 bước/host | Biến đổi |
| **Tổng** | | | **~40-60 kết quả/lần** |

- **Trạng thái tổng thể (Overall Status):**
  - `CRITICAL` – có ít nhất 1 CRITICAL hoặc ERROR
  - `WARNING` – có ít nhất 1 WARNING, không CRITICAL/ERROR
  - `OK` – tất cả đều OK
- Dynamic modules:
  - được lưu trong SQLite
  - chạy độc lập theo lịch riêng
  - dùng SSH command steps với rule-based evaluation

---

## 2. Sơ đồ kiến trúc

```
┌─────────────────────────────────────────────────────────────────┐
│                         main.py                                  │
│                    Entry Point / CLI                            │
└────────────┬────────────────────────────────────────────────┬──┘
             │                                             │
             ▼                                             ▼
    ┌─────────────────┐                    ┌──────────────────────┐
    │   Argument      │                    │   Config Manager     │
    │   Parser        │                    │   (load YAML)        │
    └────────┬────────┘                    └──────────┬───────────┘
             │                                        │
    ┌────────▼──────────────────────────────────────▼────────┐
    │                    setup_logging()                   │
    └──────────────────────────────────────────────────────┘
             │
    ┌────────▼───────────────┐
    │  Mode quyết định:      │
    │  --run-now / --scheduler / --dashboard / default  │
    └────────┬───────────────┘
             │
    ┌────────▼──────────────────────────────────────────────────┐
    │                                                        │
    ▼                                                        ▼
┌───────────────┐                                    ┌─────────────────┐
│ run_all_now() │                                    │ scheduler.py    │
│ (orchestrator)│                                    │ (APScheduler)   │
└───────┬───────┘                                    └────────┬────────┘
        │                                                     │
        │                    ┌───────────────────────────────┘
        │                    │  trigger _job_wrapper()
        ▼                    ▼
┌───────────────────────────────────────────────────────┐
│                   Orchestrator                         │
│           run_daily_check()                           │
│                                                       │
│  ThreadPoolExecutor (max_workers=10)                  │
│  ┌──────────┬──────────┬──────────┬──────────┐      │
│  │ Oracle   │ MongoDB  │ Hadoop  │ App      │      │
│  │ Checks   │ Checks   │ Checks  │ Checks   │      │
│  └──────────┴──────────┴──────────┴──────────┘      │
│                        │                              │
│                        │ List[CheckResult]            │
│                        ▼                              │
│               ┌─────────────┐                        │
│               │ ResultStore  │                        │
│               │ (SQLite)     │                        │
│               └─────────────┘                        │
│                        │                              │
│               ┌────────▼─────────┐                  │
│               │ AlertManager     │                  │
│               │ (Email+Webhook)  │                  │
│               └────────┬─────────┘                  │
│                        │                              │
│               ┌────────▼─────────┐                  │
│               │ReportGenerator   │                  │
│               │ (HTML Email)     │                  │
│               └──────────────────┘                  │
└──────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────┐
    │              Dashboard (Flask)               │
    │                                              │
    │  /                – Trang chủ, danh sách run │
    │  /run/<run_id>    – Chi tiết từng run        │
    │  /report/<run_id> – Xem báo cáo HTML         │
    │  /api/status      – Health check JSON        │
    │  /api/trigger     – Trigger manual           │
    └──────────────────────────────────────────────┘
```

---

## 3. Cấu trúc thư mục

```
simo_dailycheck/
│
├── main.py                  # Entry point, CLI parser, mode routing
│
├── config/
│   └── config.yaml          # File cấu hình YAML (không commit Git)
│
├── src/
│   ├── __init__.py
│   ├── config_manager.py    # Load & parse YAML, global get()
│   ├── logger.py            # Logging setup + SQLite ResultStore
│   ├── ssh_manager.py       # SSH connection pool (Paramiko)
│   ├── orchestrator.py      # Điều phối chạy song song + tổng hợp kết quả
│   ├── scheduler.py         # APScheduler CronTrigger (8:00 daily)
│   ├── alert_manager.py     # Gửi email/webhook, chống spam
│   ├── dynamic_modules.py   # Registry + validation + runner cho dynamic modules
│   ├── report_generator.py  # Tạo HTML report cho email
│   ├── security.py          # Encrypt/decrypt stored passwords
│   │
│   └── checks/
│       ├── __init__.py
│       ├── oracle_checks.py  # 11 bước Oracle RAC
│       ├── mongo_checks.py  # 3 bước MongoDB
│       ├── hadoop_checks.py  # 3 bước Hadoop
│       └── app_checks.py     # 3-4 bước × N apps
│
├── dashboard/
│   ├── __init__.py
│   ├── app.py               # Flask app + routes
│   └── templates/
│       ├── base.html
│       ├── index.html
│       ├── modules.html
│       ├── module_form.html
│       └── run_detail.html
│
├── data/
│   ├── dailycheck.db        # SQLite database
│   └── reports/             # HTML report files
│       └── report_<run_id>.html
│
├── logs/
│   └── dailycheck_YYYYMMDD.log
│
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## 4. Luồng hoạt động chính

### 4.1. Khởi động hệ thống

```
python main.py
    │
    ├─ load_config()         # Đọc config/config.yaml
    ├─ setup_logging()       # Cấu hình logging + file log
    │
    ├─ start_scheduler()     # APScheduler: cron daily 08:00
    │
    └─ Flask dashboard       # Chạy trong daemon thread
        http://0.0.0.0:5000
```

### 4.2. Daily Check Flow (Mỗi lần chạy)

```
Scheduled Trigger (08:00) HOẶC Manual Trigger HOẶC --run-now
    │
    ▼
Orchestrator.run_daily_check()
    │
    ├─ Tạo run_id = "YYYYMMDD_HHMMSS_<uuid6>"
    ├─ Lưu RunSummary vào SQLite (trạng thái bắt đầu)
    │
    ├─ ThreadPoolExecutor(max_workers=10)
    │     │
    │     ├─ Thread 1: oracle_checks.run_all()
    │     │       ├─ SSH to 10.1.36.11 (DB1)
    │     │       │    ├─ check_system_info      (B1)
    │     │       │    ├─ check_disk_space        (B2)
    │     │       │    ├─ check_crs_status        (B3) – node 1 only
    │     │       │    ├─ check_ocr               (B4) – node 1 only
    │     │       │    ├─ check_voting_disk       (B5) – node 1 only
    │     │       │    ├─ check_archive_switch    (B6) – node 1 only
    │     │       │    ├─ check_free_tablespace   (B7) – node 1 only
    │     │       │    ├─ check_unusable_indexes  (B8) – node 1 only
    │     │       │    ├─ check_invalid_objects   (B9) – node 1 only
    │     │       │    ├─ check_asm_diskgroup     (B10) – node 1 only
    │     │       │    └─ check_alert_log          (B11) – node 1 only
    │     │       │
    │     │       └─ SSH to 10.1.36.12 (DB2)
    │     │            ├─ check_system_info      (B1)
    │     │            └─ check_disk_space         (B2)
    │     │
    │     ├─ Thread 2: mongo_checks.run_all()
    │     │       ├─ SSH to 10.1.36.41 (Mongo1) – 3 checks
    │     │       └─ SSH to 10.1.36.44 (Mongo2) – 3 checks
    │     │
    │     ├─ Thread 3: hadoop_checks.run_all()
    │     │       ├─ SSH to 10.1.36.31 (Node1) – 3 checks
    │     │       ├─ SSH to 10.1.36.34 (Node2) – 3 checks
    │     │       └─ SSH to 10.1.36.37 (Node3) – 3 checks
    │     │
    │     └─ Thread 4: app_checks.run_all()
    │             ├─ WSO2 EI Cluster (2 hosts × 3-4 checks)
    │             ├─ WSO2 APIM Cluster (2 hosts × 3-4 checks)
    │             ├─ HAProxy (2 hosts × 3 checks)
    │             ├─ Kafka (2 hosts × 3 checks)
    │             └─ Application Server (3 hosts × 4 checks)
    │
    ├─ collect List[CheckResult] từ tất cả threads
    │
    ├─ Lưu toàn bộ CheckResult vào SQLite
    │
    ├─ Tính overall_status:
    │     if CRITICAL or ERROR → CRITICAL
    │     elif WARNING        → WARNING
    │     else                → OK
    │
    ├─ Update RunSummary trong SQLite
    │
    ├─ AlertManager gửi alert (nếu CRITICAL/ERROR, chống spam)
    │
    ├─ ReportGenerator tạo HTML report
    │
    └─ Lưu HTML vào data/reports/report_<run_id>.html
```

### 4.3. Timing Diagram

```
Time        Activity
────────────────────────────────────────────────────────
07:55:00   Prep job (SSH connection warming - không làm gì khác)
08:00:00   [SCHEDULED] Daily Check bắt đầu
           │
           ├─ T0+0s     Orchestrator khởi tạo, tạo run_id
           ├─ T0+1s     4 check modules bắt đầu song song
           │             (Oracle, MongoDB, Hadoop, App)
           │
           ├─ T0+2-30s  SSH connections established
           │             Các lệnh SQL + shell thực thi
           │
           ├─ T0+30-60s Results từ từ quay về
           │
           ├─ T0+60s    Tất cả module hoàn thành
           ├─ T0+62s    Tổng hợp, lưu SQLite
           ├─ T0+63s    Gửi email + webhook (nếu có)
           ├─ T0+64s    Lưu HTML report
           └─ T0+65s    Hoàn thành
```

### 4.4. Dynamic Module Flow

```
User tạo/sửa module trong Dashboard
    │
    ▼
dashboard/app.py → DynamicModuleStore
    │
    ├─ lưu module metadata
    ├─ lưu hosts
    ├─ lưu steps + rules
    └─ resync APScheduler jobs

Scheduled Trigger HOẶC POST /api/modules/<id>/trigger
    │
    ▼
scheduler._dynamic_job_wrapper(module_id)
    │
    ▼
orchestrator.run_dynamic_module(module_id)
    │
    ├─ load module từ SQLite
    ├─ tạo RunSummary(run_type="dynamic_module")
    ├─ chạy từng SSH command step
    ├─ evaluate status:
    │    critical keyword > warning keyword > non-zero exit mapping > OK
    ├─ save check_results
    ├─ generate HTML report riêng cho module
    └─ send email tới recipients của chính module
```

---

## 5. Chi tiết từng module

### 5.1. config_manager.py

```python
# Singleton pattern – load 1 lần, truy cập global
load_config("/path/to/config.yaml")   # Gọi trước tất cả
get("oracle.hosts[0].ip")              # Truy cập nested value
get("general.ssh_timeout", default=30) # Với default value
```

**Cấu trúc config:**
```yaml
general:          # Cấu hình chung
ssh_defaults:     # SSH mặc định
oracle:           # Oracle RAC hosts + instances
mongodb:          # MongoDB hosts
hadoop:           # Hadoop nodes + namenode
applications:     # Danh sách app (WSO2, HAProxy, Kafka,...)
thresholds:       # Ngưỡng cảnh báo
notifications:    # Email + Webhook
dashboard:        # Flask host/port/secret
```

### 5.2. ssh_manager.py

```
SSHClient
    ├─ connect(max_retries=2)    # Paramiko, retry 2 lần
    ├─ run(command, timeout)     # exec_command + recv_exit_status
    └─ run_sudo(command)         # prefix sudo

ssh_connect(host_cfg)            # Context manager
    ├─ make_ssh_config(host_cfg) # Từ dict → SSHConfig
    ├─ SSHClient.connect()
    ├─ yield client
    └─ client.close() ở finally
```

**SSH Connection Flow:**
```
1. Load key_path từ config (~ → /home/user)
2. Paramiko SSHClient.set_missing_host_key_policy(AutoAddPolicy)
3. connect() với key_filename hoặc password
4. exec_command() với timeout
5. recv_exit_status() để lấy exit code
```

### 5.3. orchestrator.py

```python
def run_daily_check() -> RunSummary:
    # 1. Tạo run_id duy nhất
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    # 2. ThreadPoolExecutor với 4 modules
    with ThreadPoolExecutor(max_workers=min(10, 4)) as executor:
        futures = {
            executor.submit(fn): name
            for name, fn in modules
        }

    # 3. as_completed() – kết quả về đến đâu xử lý đến đó

    # 4. overall_status = _overall_status(all_results)
    #    CRITICAL if any CRITICAL/ERROR, else WARNING if any WARNING, else OK

    # 5. Gửi report + lưu HTML
```

### 5.4. scheduler.py

```python
# APScheduler BackgroundScheduler
trigger = CronTrigger(
    hour=8, minute=0,
    timezone="Asia/Ho_Chi_Minh",
    misfire_grace_time=300,  # Cho phép trễ 5 phút
)

# _job_wrapper có lock chống overlap
# Nếu đang chạy mà trigger tiếp → bỏ qua
```

### 5.5. Check Modules

#### Oracle (oracle_checks.py) – 11 bước

| Bước | Tên | Lệnh SSH/SQL | Threshold |
|------|-----|---------------|-----------|
| B1 | System Info | `date`, `hostname` | – |
| B2 | Disk Space | `df -k` | ≥90% → CRITICAL |
| B3 | CRS Status | `crs_stat -t` | Target≠State → CRITICAL |
| B4 | OCR Check | `ocrcheck` | integrity failed → CRITICAL |
| B5 | Voting Disk | `crsctl query css votedisk` | ≠3 ONLINE → CRITICAL |
| B6 | Archive Switch | `sqlplus ... alter system archive log current` | ORA- → CRITICAL |
| B7 | Free Tablespace | SQL: dba_free_space | SYSTEM/SYSAUX <3%, others <15% → CRITICAL |
| B8 | Unusable Indexes | SQL: dba_indexes | >0 → WARNING |
| B9 | Invalid Objects | SQL: dba_objects | >0 → WARNING |
| B10 | ASM Disk Group | SQL: v$asm_diskgroup | ≥90% → CRITICAL |
| B11 | Alert Log | `tail -1000 alert_*.log \| egrep 'ORA-\|TNS-'` | >0 → WARNING |

#### MongoDB (mongo_checks.py) – 3 bước

| Bước | Tên | Lệnh SSH |
|------|-----|----------|
| B1 | System Info | `date`, `hostname` |
| B2 | Disk Space | `df -kh` |
| B3 | mongod Service | `systemctl status mongod` |

#### Hadoop (hadoop_checks.py) – 3 bước

| Bước | Tên | Lệnh SSH |
|------|-----|----------|
| B1 | System Info | `date`, `hostname` |
| B2 | Disk Space | `df -kh` |
| B3 | HDFS Report | `hdfs dfsadmin -report` |

#### Application (app_checks.py) – 3-4 bước/host

| Bước | Tên | Lệnh SSH |
|------|-----|----------|
| B1 | System Info | `date`, `hostname` |
| B2 | Disk Space | `df -kh` |
| B3 | Service Status | `systemctl status <service>` |
| B3b (optional) | Docker Containers | `docker ps`, `docker service ls` |

---

## 6. Mô hình dữ liệu

### 6.1. SQLite Schema

```sql
-- check_runs: Mỗi lần chạy Daily Check
CREATE TABLE check_runs (
    run_id          TEXT PRIMARY KEY,     -- "20260416_080000_abc123"
    started_at      TEXT NOT NULL,         -- ISO timestamp
    run_type        TEXT NOT NULL DEFAULT 'builtin',
    module_id       INTEGER,
    module_name     TEXT DEFAULT '',
    finished_at     TEXT,
    overall_status  TEXT DEFAULT 'OK',      -- OK / WARNING / CRITICAL
    total_checks    INTEGER DEFAULT 0,
    ok_count        INTEGER DEFAULT 0,
    warning_count   INTEGER DEFAULT 0,
    critical_count  INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0
);

-- check_results: Mỗi bước kiểm tra
CREATE TABLE check_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    system      TEXT NOT NULL,              -- Oracle / MongoDB / Hadoop / WSO2 EI...
    host        TEXT NOT NULL,              -- IP hoặc hostname
    check_name  TEXT NOT NULL,              -- "Bước 3 – CRS Status"
    status      TEXT NOT NULL,              -- OK / WARNING / CRITICAL / ERROR
    message     TEXT,                       -- Mô tả ngắn kết quả
    raw_output  TEXT,                       -- Output thô từ lệnh
    details     TEXT,                       -- JSON dict (parsed later)
    checked_at  TEXT NOT NULL,              -- ISO timestamp
    FOREIGN KEY (run_id) REFERENCES check_runs(run_id)
);

-- alert_history: Lịch sử gửi alert
CREATE TABLE alert_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    system      TEXT NOT NULL,
    host        TEXT NOT NULL,
    check_name  TEXT NOT NULL,
    status      TEXT NOT NULL,
    message     TEXT,
    alerted_at  TEXT NOT NULL,
    channel     TEXT NOT NULL              -- email / webhook
);

CREATE INDEX idx_results_run ON check_results(run_id);
CREATE INDEX idx_runs_started ON check_runs(started_at);

CREATE TABLE dynamic_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    system_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    timezone TEXT NOT NULL,
    daily_time TEXT NOT NULL,
    recipient_emails TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dynamic_module_hosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    username TEXT NOT NULL,
    auth_type TEXT NOT NULL DEFAULT 'password',
    encrypted_password TEXT DEFAULT '',
    key_path TEXT DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE dynamic_module_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    timeout_sec INTEGER NOT NULL DEFAULT 60,
    run_on_each_host INTEGER NOT NULL DEFAULT 1,
    rules_json TEXT NOT NULL DEFAULT '{}',
    sort_order INTEGER NOT NULL DEFAULT 0
);
```

### 6.2. Data Classes

```python
@dataclass
class CheckResult:
    system: str              # "Oracle"
    host: str                # "DB1"
    check_name: str          # "Bước 3 – CRS Status"
    status: str               # "OK" / "WARNING" / "CRITICAL" / "ERROR"
    message: str              # "Tất cả CRS resource đồng bộ"
    raw_output: str = ""      # Output thô từ SSH command
    details: dict = {}        # {"overloaded": ["/u01"]}
    checked_at: str = ""     # ISO timestamp

@dataclass
class RunSummary:
    run_id: str
    started_at: str
    run_type: str = "builtin"
    module_id: int | None = None
    module_name: str = ""
    finished_at: str = ""
    overall_status: str = "OK"
    total_checks: int = 0
    ok_count: int = 0
    warning_count: int = 0
    critical_count: int = 0
    error_count: int = 0
    results: list = field(default_factory=list)
```

---

## 7. Cơ chế xử lý song song

### 7.1. ThreadPoolExecutor

```python
# Trong orchestrator.py
modules = [
    ("Oracle", oracle_checks.run_all),
    ("MongoDB", mongo_checks.run_all),
    ("Hadoop", hadoop_checks.run_all),
    ("Application", app_checks.run_all),
]

with ThreadPoolExecutor(max_workers=min(10, len(modules))) as executor:
    futures = {
        executor.submit(fn, alert_mgr): name
        for name, fn in modules
    }
    for future in as_completed(futures):
        module_name = futures[future]
        try:
            results = future.result()
            all_results.extend(results)
        except Exception as e:
            logger.exception(f"Module [{module_name}] failed: {e}")
```

### 7.2. SSH Connection Pool

- **Mỗi check module** tự quản lý SSH connections cho các host của nó
- `ssh_connect(host_cfg)` là context manager đảm bảo `close()` được gọi
- Retry logic: 2 lần retry với 2s delay
- Timeout mặc định: 30s connect, 60s command (có thể override per-command)
- Dynamic modules dùng `SSHClient` trực tiếp với host config lưu trong SQLite

### 7.3. Anti-overlap

```python
# scheduler.py
_is_running = False

def _job_wrapper():
    global _is_running
    with _last_run_lock:
        if _is_running:
            logger.warning("Daily Check đang chạy, bỏ qua lần trigger này")
            return
        _is_running = True
    try:
        run_daily_check()
    finally:
        _is_running = False
```

- Built-in run có anti-overlap toàn cục
- Dynamic modules có anti-overlap theo từng `module_id`

---

## 8. Scheduler & Timing

### 8.1. APScheduler Configuration

```python
scheduler = BackgroundScheduler(timezone="Asia/Ho_Chi_Minh")

trigger = CronTrigger(
    hour=8,
    minute=0,
)

scheduler.add_job(
    _job_wrapper,
    trigger=trigger,
    id="daily_check",
    misfire_grace_time=300,  # 5 phút
    replace_existing=True,
)
```

- Ngoài job `daily_check`, mỗi dynamic module enabled tạo thêm một job `dynamic_module_<id>`
- `resync_dynamic_jobs()` được gọi sau create/update/delete module

### 8.2. Prep Time (07:55)

- Hiện tại `prep_time` trong config nhưng **chưa được sử dụng** trong code
- Mục đích thiết kế: SSH connection warming vào 07:55, để 08:00 chạy nhanh hơn
- Có thể implement trong tương lai

### 8.3. Các thành phần chạy độc lập

```bash
python main.py --run-now      # Chạy 1 lần, không scheduler, không dashboard
python main.py --dashboard    # Chỉ Flask dashboard
python main.py --scheduler    # Chỉ scheduler, không dashboard
python main.py                # Cả hai (default)
```

---

## 9. Dashboard & API

### 9.1. Flask Routes

| Route | Method | Mô tả |
|-------|--------|-------|
| `/` | GET | Trang chủ – danh sách 30 run gần nhất |
| `/run/<run_id>` | GET | Chi tiết 1 run – bảng kết quả nhóm theo system |
| `/report/<run_id>` | GET | Xem file HTML report đã lưu |
| `/api/status` | GET | JSON: `{is_running, latest_run, server_time}` |
| `/api/trigger` | POST | Manual trigger Daily Check |
| `/api/runs` | GET | JSON: danh sách 30 run |
| `/api/run/<run_id>` | GET | JSON: kết quả chi tiết 1 run |
| `/modules` | GET | UI danh sách dynamic modules |
| `/modules/new` | GET | UI tạo dynamic module |
| `/modules/<id>/edit` | GET | UI sửa dynamic module |
| `/api/modules` | GET/POST | List hoặc create dynamic module |
| `/api/modules/<id>` | GET/PUT/DELETE | Read/update/delete dynamic module |
| `/api/modules/<id>/trigger` | POST | Trigger dynamic module |
| `/api/modules/<id>/runs` | GET | Lịch sử run của module |

### 9.2. Dashboard UI

```
┌──────────────────────────────────────────────────────────────┐
│  🏠 SIMO Daily Check Dashboard          16/04/2026 08:05:30   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Trạng thái: ✅ OK         Đang chạy: Có                    │
│  Dynamic Modules: list / edit / run now                    │
│                                                              │
│  ── 30 lần chạy gần đây ──────────────────────────────────  │
│  Run ID                    Thời gian      T.Trạng  OK  WARN  │
│  20260416_080000_abc123    08:00 hôm nay  ✅OK    45  2   0  │
│  20260415_080012_def456   08:00 hôm qua  ⚠️WARN  42  5   0  │
│  ...                                                            │
│                                                              │
│  [Chi tiết] [Xem Report]                                    │
└──────────────────────────────────────────────────────────────┘
```

### 9.3. Health Check

```bash
curl http://localhost:5000/api/status
# Response:
{
  "is_running": false,
  "latest_run": {
    "run_id": "20260416_080000_abc123",
    "run_type": "builtin",
    "overall_status": "OK",
    "started_at": "2026-04-16T08:00:00"
  },
  "server_time": "2026-04-16T08:05:30.123456"
}
```

---

## 10. Alert & Notification

### 10.1. Alert Flow

```
CheckResult with status=CRITICAL/ERROR
    │
    ▼
AlertManager.should_alert(result)
    ├─ _reset_if_new_day()  ← Clear spam cache nếu sang ngày mới
    ├─ status ≠ CRITICAL/ERROR → return False
    ├─ key = f"{system}|{host}|{check_name}"
    ├─ key đã alert hôm nay → return False
    └─ key chưa alert → return True, mark as alerted

if should_alert():
    ├─ Email (SMTP)
    └─ Webhook (Slack/Teams HTTP POST)
```

### 10.2. Anti-Spam

- Mỗi `(system, host, check_name)` chỉ alert **1 lần/ngày**
- `_alerted_today` set được clear khi midnight

### 10.3. Report (không spam)

- Báo cáo HTML tổng hợp gửi **mỗi lần chạy** (không spam filter)
- Gửi sau khi tất cả checks hoàn thành
- Built-in runs dùng recipient list toàn cục trong config
- Dynamic module runs override recipients theo cấu hình module

---

## 11. Bảo mật

### 11.1. SSH Authentication

- **Ưu tiên SSH Key** (`~/.ssh/id_rsa`)
- Password chỉ dùng khi không có key (`CHANGE_ME` placeholder trong config)
- Config file không commit lên Git (`.gitignore` exclude `config/config.yaml`)

### 11.2. Database

- SQLite file trong `data/` – không commit lên Git
- Không có mã hóa (development); production nên dùng SQLCipher hoặc separate DB server
- Dynamic module passwords được mã hóa ở cấp ứng dụng trước khi ghi vào SQLite

### 11.3. Secrets

```yaml
# config.yaml – tất cả secrets đều là placeholders
db_connection:
  password: "CHANGE_ME"   # Phải thay bằng password thực

notifications:
  email:
    password: "CHANGE_ME"
security:
  encryption_key: "CHANGE_ME_DYNAMIC_MODULE_SECRET"
  webhook:
    url: "https://hooks.slack.com/services/CHANGE_ME"

dashboard:
  secret_key: "CHANGE_ME_USE_RANDOM_STRING"
```

### 11.4. Docker Security

- SSH keys mounted read-only: `${HOME}/.ssh:/root/.ssh:ro`
- Config file mounted read-only: `config/config.yaml:ro`
- Container không chạy as root (có thể thêm `USER` trong Dockerfile)

---

## 12. Luồng dữ liệu tổng hợp

```
Config YAML
    │
    ▼
config_manager.load_config()
    │
    ├──► get("general.ssh_timeout") → ssh_manager.py
    ├──► get("oracle.hosts") → oracle_checks.py
    ├──► get("mongodb.hosts") → mongo_checks.py
    ├──► get("hadoop.nodes") → hadoop_checks.py
    └──► get("applications") → app_checks.py

Dynamic Module Registry
    │
    ▼
SQLite: dynamic_modules / dynamic_module_hosts / dynamic_module_steps
    │
    ├──► scheduler.resync_dynamic_jobs()
    ├──► orchestrator.run_dynamic_module(module_id)
    └──► dashboard module editor UI / APIs

SSH Connection
    │
    ▼
Each check module:
    │  SSH to host
    │  Run command (df, crs_stat, sqlplus, hdfs dfsadmin, etc.)
    │  Parse output
    │  Return CheckResult
    │
    ▼
orchestrator.run_daily_check():
    │  ThreadPoolExecutor.join() → List[CheckResult]
    │  ResultStore.save_result() → SQLite
    │  _overall_status() → "OK" / "WARNING" / "CRITICAL"
    │
    ▼
AlertManager.send_report() → Email / Webhook
ReportGenerator.generate_html_report() → data/reports/report_<run_id>.html

Dashboard:
    │  ResultStore.get_recent_runs() → HTML table
    │  ResultStore.get_run_results() → Chi tiết từng run
    │  Flask render_template() → Web UI
    │
    ▼
User Browser → http://localhost:5000
```
