# SIMO Daily Check System

Hệ thống kiểm tra tự động hàng ngày cho Oracle RAC, MongoDB, Hadoop và ứng dụng.

## Mục lục

1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cài đặt nhanh](#2-cài-đặt-nhanh)
3. [Cài đặt chi tiết](#3-cài-đặt-chi-tiết)
4. [Cấu hình](#4-cấu-hình)
5. [Chạy hệ thống](#5-chạy-hệ-thống)
6. [Dashboard](#6-dashboard)
7. [Xem báo cáo](#7-xem-báo-cáo)
8. [Docker](#8-docker)
9. [Xử lý sự cố](#9-xử-lý-sự-cố)

---

## 1. Yêu cầu hệ thống

- **OS:** Linux (Ubuntu 20.04+, CentOS 7+)
- **Python:** 3.9+
- **SSH:** Truy cập vào các máy chủ Oracle, MongoDB, Hadoop, Application
- **Network:** Các port cần thiết đã mở giữa hệ thống dailycheck và các máy chủ

### Các máy chủ cần kiểm tra

| Hệ thống | Số lượng Node | Port SSH |
|----------|--------------|----------|
| Oracle RAC | 2+ | 22 |
| MongoDB | 2+ | 22 |
| Hadoop | 3+ | 22 |
| Application | 1-3+ mỗi ứng dụng | 22 |

---

## 2. Cài đặt nhanh

```bash
# 1. Clone/Download project
cd simo_dailycheck

# 2. Tạo virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Cài đặt dependencies
pip install -r requirements.txt

# 4. Cấu hình SSH keys
ssh-keygen -t rsa -b 4096
ssh-copy-id user@oracle-server
ssh-copy-id user@mongo-server
# ... (lặp lại cho tất cả các máy chủ)

# 5. Cấu hình config
cp config/config.yaml config/config.yaml.backup
# Chỉnh sửa config/config.yaml với thông tin thực tế

# 6. Chạy thử
python main.py --run-now
```

---

## 3. Cài đặt chi tiết

### 3.1. Cài đặt Python và dependencies

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y python3.9 python3-pip python3-venv

# Tạo virtual environment
python3 -m venv venv
source venv/bin/activate

# Cài đặt packages
pip install -r requirements.txt
```

### 3.2. Cài đặt SSH Keys

**Phương pháp 1: SSH Key (Khuyến nghị)**

```bash
# Tạo SSH key
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa

# Copy public key lên các máy chủ
ssh-copy-id -i ~/.ssh/id_rsa.pub oracle@10.1.36.11
ssh-copy-id -i ~/.ssh/id_rsa.pub oracle@10.1.36.12
ssh-copy-id -i ~/.ssh/id_rsa.pub root@10.1.36.41
# ... thêm các máy chủ khác
```

**Phương pháp 2: Password**

Chỉnh sửa `config/config.yaml`:
```yaml
ssh_defaults:
  password: "your_password_here"
```

### 3.3. Cấu trúc thư mục

```
simo_dailycheck/
├── config/
│   └── config.yaml          # File cấu hình chính
├── data/
│   ├── dailycheck.db        # SQLite database
│   └── reports/             # Báo cáo HTML
├── logs/                    # Log files
├── src/
│   ├── checks/              # Các module kiểm tra
│   ├── config_manager.py
│   ├── orchestrator.py
│   ├── scheduler.py
│   └── ...
├── dashboard/               # Flask web dashboard
├── main.py                  # Entry point
├── requirements.txt
├── docker-compose.yml
└── Dockerfile
```

---

## 4. Cấu hình

### 4.1. File cấu hình chính

Mở file `config/config.yaml` và chỉnh sửa:

```yaml
# Cấu hình chung
general:
  check_time: "08:00"        # Giờ chạy kiểm tra (HH:MM)
  ssh_timeout: 30            # Timeout SSH (giây)

# SSH mặc định
ssh_defaults:
  port: 22
  username: "oracle"
  key_path: "~/.ssh/id_rsa"

# Oracle RAC
oracle:
  db_name: "Simo_master"
  hosts:
    - name: "DB1"
      ip: "10.1.36.11"
      instance: "simo1"
      asm_instance: "+ASM1"   # (tuỳ chọn) SID của ASM cho Bước 10, mặc định +ASM1
      ssh:
        username: "oracle"
        key_path: "~/.ssh/id_rsa"

# MongoDB
mongodb:
  hosts:
    - name: "Mongo1"
      ip: "10.1.36.41"
      ssh:
        username: "root"
        key_path: "~/.ssh/id_rsa"

# Hadoop
hadoop:
  namenode_ip: "10.1.36.31"
  nodes:
    - name: "HadoopNode1"
      ip: "10.1.36.31"
      ssh:
        username: "hdfs"
        key_path: "~/.ssh/id_rsa"
```

### 4.2. Ngưỡng cảnh báo (Thresholds)

```yaml
thresholds:
  disk_usage_max_pct: 90     # Cảnh báo disk >= 90%
  asm_usage_max_pct: 90     # Cảnh báo ASM >= 90%
  voting_disk_required: 3   # Cần 3 voting disk ONLINE
  tablespace:
    system_min_free_pct: 3   # SYSTEM/SYSAUX >= 3% free
    undotbs_min_total_mb: 400 # UNDOTBS >= 400MB total
    default_min_free_pct: 15 # Tablespace khác >= 15% free
```

### 4.3. Thông báo Email/Webhook

```yaml
security:
  encryption_key: "CHANGE_ME_DYNAMIC_MODULE_SECRET"

notifications:
  email:
    enabled: true
    smtp_host: "smtp.company.com"
    smtp_connect_host: ""   # Optional: IP/host actually used to connect if DNS for smtp_host is unavailable
    smtp_port: 587
    smtp_use_tls: true      # STARTTLS on plain SMTP ports like 587
    smtp_use_ssl: false     # Implicit SSL, typically port 465
    sender: "dailycheck@company.com"
    username: "dailycheck@company.com"   # Optional: login name if different from sender
    password: "CHANGE_ME"
    smtp_require_auth: true # Set false for internal relay servers that do not support AUTH
    recipients:
      - "ops@company.com"

  webhook:
    enabled: true
    url: "https://hooks.slack.com/services/XXX"
```

### 4.4. Dynamic Modules

- Built-in Oracle, MongoDB, Hadoop, and Application checks keep hosts and thresholds in `config.yaml`; their commands are editable on the dashboard (see §4.5).
- New user-defined modules are managed from the dashboard and stored in SQLite.
- Each dynamic module has `name`, `system_name`, `enabled`, `timezone`, `daily_time`, recipients, SSH hosts, ordered command steps, and per-step status rules.
- Step status is determined by: critical keyword match, then warning keyword match, then non-zero exit handling, else OK.
- Dynamic module SSH passwords are stored encrypted using `security.encryption_key`.

### 4.5. Lệnh check built-in

Lệnh shell/SQL của các check built-in (Oracle 11 bước, MongoDB, Hadoop, Application) được chỉnh sửa tại trang `/commands` trên dashboard (menu **Lệnh Built-in**):

- Mỗi bước cho phép sửa lệnh, SQL script (các bước Oracle dùng sqlplus), timeout và bật/tắt bước.
- Thay đổi lưu vào bảng `builtin_step_overrides` trong SQLite và áp dụng từ lần chạy tiếp theo, không cần restart. Nút **Khôi phục mặc định** xoá phần đã sửa.
- Lệnh mặc định nằm trong `src/builtin_commands.py`. Logic đánh giá output (ngưỡng, từ khoá) vẫn nằm trong `src/checks/*_checks.py` và `thresholds.*`, nên lệnh sửa phải giữ output đúng định dạng mô tả ở từng bước.
- Lệnh viết trên một dòng, dùng biến dạng `{{var}}`: `{{host_name}}`, `{{host_ip}}`, Oracle thêm `{{instance}}`, `{{asm_instance}}` (Bước 11 có `{{alert_dir}}`), MongoDB/Application thêm `{{service_name}}`, Application thêm `{{app_name}}`.
- SQL script được gửi vào stdin của lệnh (không qua `echo`), nên `v$asm_diskgroup`, dấu nháy trong SQL giữ nguyên.
- Áp dụng cùng quy tắc chặn như dynamic module: không cho `$(`, backtick, `sudo`/`su`, `rm -rf /`, redirect vào `/dev/*`.

Ví dụ: nếu user SSH chưa có `ORACLE_HOME` của grid, sửa lệnh Bước 10 thành
`export ORACLE_HOME=/u01/app/19.3.0/grid; export ORACLE_SID={{asm_instance}}; $ORACLE_HOME/bin/sqlplus -S / as sysasm`.

---

## 5. Chạy hệ thống

### 5.1. Chạy thủ công (one-time)

```bash
python main.py --run-now
```

### 5.2. Chạy đầy đủ (scheduler + dashboard)

```bash
python main.py
```

- Scheduler chạy tự động vào 8:00 sáng, Thứ 2-6
- Dashboard web tại http://localhost:5000
- Dynamic modules also get their own daily scheduler jobs based on each module's configured `daily_time`

### 5.3. Chạy riêng lẻ

```bash
# Chỉ dashboard
python main.py --dashboard

# Chỉ scheduler
python main.py --scheduler

# File config tùy chỉnh
python main.py --config /path/to/config.yaml
```

### 5.4. Các tùy chọn command line

| Tùy chọn | Mô tả |
|----------|-------|
| `--run-now` | Chạy kiểm tra ngay và thoát |
| `--dashboard` | Chỉ chạy web dashboard |
| `--scheduler` | Chỉ chạy scheduler |
| `--config <file>` | Dùng file config tùy chỉnh |
| `--log-level` | DEBUG, INFO, WARNING, ERROR |

---

## 6. Dashboard

### 6.1. Truy cập Dashboard

Mở trình duyệt: http://localhost:5000

### 6.2. Các chức năng

| Trang | URL | Mô tả |
|-------|-----|-------|
| Trang chính | `/` | Danh sách các lần chạy |
| Chi tiết | `/run/<run_id>` | Kết quả chi tiết từng bước kiểm tra |
| Dynamic modules | `/modules` | Danh sách module do người dùng khai báo |
| Tạo module | `/modules/new` | Tạo mới module động |
| Sửa module | `/modules/<id>/edit` | Sửa hosts, steps, schedule, recipients |
| Lệnh built-in | `/commands` | Sửa lệnh/SQL/timeout, bật/tắt từng bước check built-in |
| Health check | `/api/status` | Trả về JSON trạng thái hệ thống |

### 6.3. API Endpoints

```bash
# Health check
curl http://localhost:5000/api/status

# Kết quả trả về
{
  "is_running": false,
  "latest_run": {
    "run_id": "20260504_080000_abc123",
    "run_type": "builtin",
    "overall_status": "OK",
    "started_at": "2026-05-04T08:00:00"
  },
  "server_time": "2026-05-04T08:05:30.123456"
}
```

Dynamic module APIs:

| Route | Method | Mô tả |
|-------|--------|-------|
| `/api/modules` | GET | Danh sách dynamic modules + next run + last run |
| `/api/modules` | POST | Tạo module mới |
| `/api/modules/<id>` | GET | Chi tiết 1 module |
| `/api/modules/<id>` | PUT | Cập nhật module |
| `/api/modules/<id>` | DELETE | Xóa module |
| `/api/modules/<id>/trigger` | POST | Trigger module ngay lập tức |
| `/api/modules/<id>/runs` | GET | Lịch sử run của module |

Built-in command APIs:

| Route | Method | Mô tả |
|-------|--------|-------|
| `/api/builtin-commands` | GET | Danh sách bước built-in (lệnh hiện tại, mặc định, `is_modified`) |
| `/api/builtin-commands/<step_key>` | PUT | Lưu chỉnh sửa, thay thế toàn bộ phần đã sửa của bước (lệnh không gửi lên sẽ về mặc định): `{"enabled": true, "commands": {"main": {"command": "...", "script": "...", "timeout_sec": 60}}}` |
| `/api/builtin-commands/<step_key>` | DELETE | Khôi phục mặc định |

### 6.4. Cấu hình port

```yaml
dashboard:
  host: "0.0.0.0"  # Listen trên tất cả interface
  port: 5000
  debug: false
```

---

## 7. Xem báo cáo

### 7.1. Báo cáo HTML

Báo cáo được lưu tại: `data/reports/report_<run_id>.html`

```bash
ls -la data/reports/
```

### 7.2. Log files

```bash
ls -la logs/
tail -f logs/dailycheck_20260416.log
```

### 7.3. Database SQLite

```bash
sqlite3 data/dailycheck.db
sqlite> .tables
sqlite> SELECT * FROM check_results LIMIT 10;
sqlite> SELECT run_id, run_type, module_name, overall_status FROM check_runs ORDER BY started_at DESC LIMIT 10;
```

---

## 8. Docker

### 8.1. Build và chạy với Docker Compose

```bash
# Build image
docker-compose build

# Chạy container
docker-compose up -d

# Xem logs
docker-compose logs -f

# Stop
docker-compose down
```

### 8.2. Kiểm tra container

```bash
# Kiểm tra trạng thái
docker ps

# Health check
curl http://localhost:5000/api/status

# Shell vào container
docker exec -it simo_dailycheck bash
```

### 8.3. Lưu ý khi dùng Docker

- Mount SSH keys: `${HOME}/.ssh:/root/.ssh:ro`
- Mount config: `./config/config.yaml:/app/config/config.yaml:ro`
- Data và logs được mount ra bên ngoài để persist

---

## 9. Xử lý sự cố

### 9.1. Lỗi SSH Connection

```bash
# Kiểm tra SSH key
ssh -i ~/.ssh/id_rsa oracle@10.1.36.11

# Kiểm tra config
cat config/config.yaml | grep -A5 "ssh_defaults"
```

### 9.2. Lỗi Permission

```bash
# Sửa quyền SSH key
chmod 600 ~/.ssh/id_rsa

# Sửa quyền thư mục
chmod 700 ~/.ssh
```

### 9.3. Kiểm tra log

```bash
# Xem log gần đây
tail -100 logs/dailycheck_$(date +%Y%m%d).log

# Tìm lỗi
grep -i error logs/dailycheck_20260416.log
```

### 9.4. Kiểm tra từng module

```bash
# Chạy toàn bộ built-in checks ngay
python3 main.py --run-now

# Chạy unit tests
python3 -m unittest discover -s tests -v
```

### 9.5. Kiểm tra Database

```bash
sqlite3 data/dailycheck.db
sqlite> SELECT run_id, run_type, module_name, overall_status, started_at FROM check_runs ORDER BY started_at DESC LIMIT 5;
sqlite> SELECT id, name, system_name, enabled, daily_time FROM dynamic_modules;
```

### 9.6. Reset môi trường

```bash
# Stop và xóa container
docker-compose down

# Xóa data (nếu cần)
rm -rf data/dailycheck.db data/reports/*

# Rebuild
docker-compose up --build
```

---

## Cấu hình các ngưỡng kiểm tra

### Oracle RAC (11 bước kiểm tra)

1. **CRS Status** – Kiểm tra Cluster Ready Services
2. **Disk Activity** – I/O stat trên các disk
3. **ASM Disk Group** – Tình trạng ASM storage
4. **Voting Disk** – Đảm bảo 3 voting disk ONLINE
5. **Tablespaces** – Kiểm tra không gian
6. **Alert Log** – Phân tích alert log Oracle
7. **Archive Log** – Kiểm tra archive destinations
8. **RMAN Backup** – Xem backup gần nhất
9. **Session Count** – Số lượng session
10. **Datafiles Status** – Trạng thái datafiles
11. **Listener Status** – Listener đang chạy

### MongoDB (3 bước kiểm tra)

1. **Replication Status** – Trạng thái replica set
2. **Memory Usage** – Sử dụng memory
3. **Disk Usage** – Sử dụng disk

### Hadoop (3 bước kiểm tra)

1. **HDFS Capacity** – Dung lượng HDFS
2. **Block Status** – Trạng thái blocks
3. **Node Health** – Health các data node

### Application (3 bước × N hosts)

1. **Service Status** – Service đang chạy
2. **Process Count** – Số process
3. **Docker Replicas** – Số replica (nếu dùng Docker)

---


docker pull python:3.11-slim
docker save python:3.11-slim -o python311.tar
docker save simo-dailycheck:1.0 -o simo-dailycheck:1.0.tar

docker load -i python311.tar
docker run -d -p 5000:5000 simo-dailycheck:1.0
