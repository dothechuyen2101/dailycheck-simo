# SIMO Daily Check System
 
 Hệ thống kiểm tra tự động hàng ngày cho Oracle RAC, MongoDB, Hadoop, Application và **Dynamic Modules** (module do người dùng tự định nghĩa).
 
 Tài liệu này đóng vai trò là **project description**: công nghệ sử dụng, cấu trúc project, logic vận hành, hướng dẫn cài đặt/vận hành, Docker, cấu hình và xử lý sự cố.
 
 ## Mục lục
 
 1. [Tổng quan](#1-tổng-quan)
 2. [Công nghệ sử dụng](#2-công-nghệ-sử-dụng)
 3. [Cấu trúc project](#3-cấu-trúc-project)
 4. [Luồng vận hành (Operational Logic)](#4-luồng-vận-hành-operational-logic)
 5. [Cấu hình](#5-cấu-hình)
 6. [Cài đặt](#6-cài-đặt)
 7. [Chạy hệ thống (Usage)](#7-chạy-hệ-thống-usage)
 8. [Dashboard & API](#8-dashboard--api)
 9. [Dynamic Modules](#9-dynamic-modules)
 10. [Dữ liệu, báo cáo, logs](#10-dữ-liệu-báo-cáo-logs)
 11. [Docker](#11-docker)
 12. [Bảo mật](#12-bảo-mật)
 13. [Testing](#13-testing)
 14. [Xử lý sự cố](#14-xử-lý-sự-cố)
 
 ---
 
 ## 1. Tổng quan
 
 **SIMO Daily Check** tự động chạy các kiểm tra hạ tầng theo lịch (APScheduler) và hiển thị kết quả trên Dashboard (Flask). Hệ thống gồm:
 
 - **Built-in checks** (cố định theo code): Oracle, MongoDB, Hadoop, Application
 - **Dynamic modules** (tự định nghĩa): tạo/sửa qua UI, lưu trong SQLite, chạy theo lịch riêng
 
 **Trạng thái tổng thể của một lần chạy (overall)**:
 
 - `CRITICAL` nếu có ít nhất một kết quả `CRITICAL` hoặc `ERROR`
 - `WARNING` nếu có `WARNING` và không có `CRITICAL/ERROR`
 - `OK` nếu tất cả đều `OK`
 
 **Entry point:** `main.py`
 
 ```bash
 python main.py                      # chạy scheduler + dashboard (mặc định)
 python main.py --run-now            # chạy 1 lần ngay và thoát
 python main.py --dashboard          # chỉ chạy dashboard
 python main.py --scheduler          # chỉ chạy scheduler
 python main.py --config path.yaml   # dùng config tùy chỉnh
 ```
 
 Tài liệu kiến trúc chi tiết tham khảo thêm: `ARCHITECTURE.md`.
 
 ---
 
 ## 2. Công nghệ sử dụng
 
 - **Python**: runtime chính (Docker image dùng `python:3.11-slim`)
 - **SSH / Remote Commands**: `paramiko`
 - **Scheduler**: `APScheduler` (`BackgroundScheduler` + `CronTrigger`)
 - **Web Dashboard**: `Flask`
 - **Config**: `PyYAML` (file `config/config.yaml`)
 - **Lưu trữ**: `SQLite` (mặc định `data/dailycheck.db`)
 - **Mã hóa secrets**: `cryptography` (dùng cho password của dynamic module)
 - **Tiện ích thời gian**: `python-dateutil`
 
 Dependencies nằm trong `requirements.txt`.
 
 ---
 
 ## 3. Cấu trúc project
 
 ```
 dailycheck_simo/
 ├── main.py                  # Entry point + CLI
 ├── requirements.txt         # Python dependencies
 ├── Dockerfile               # Build image
 ├── docker-compose.yml       # Run container (mount data/logs/config/ssh keys)
 ├── config/
 │   └── config.yaml          # Cấu hình thực tế (bị gitignore)
 ├── src/
 │   ├── config_manager.py    # load YAML + get("a.b.c")
 │   ├── logger.py            # setup_logging + ResultStore (SQLite) + dataclasses
 │   ├── ssh_manager.py       # SSHClient wrapper (Paramiko)
 │   ├── orchestrator.py      # chạy built-in + finalize + report
 │   ├── scheduler.py         # APScheduler + anti-overlap + trigger
 │   ├── alert_manager.py     # gửi email/webhook
 │   ├── report_generator.py  # tạo HTML report + subject
 │   ├── security.py          # encrypt/decrypt secrets (dynamic module)
 │   ├── dynamic_modules.py   # CRUD SQLite + validate + chạy module động
 │   └── checks/
 │       ├── oracle_checks.py
 │       ├── mongo_checks.py
 │       ├── hadoop_checks.py
 │       └── app_checks.py
 ├── dashboard/
 │   ├── app.py               # Flask routes + APIs
 │   └── templates/           # UI
 ├── data/
 │   ├── dailycheck.db        # SQLite (runtime)
 │   └── reports/             # report_<run_id>.html
 └── logs/                    # log files (runtime)
 ```
 
 Lưu ý:
 
 - `config/config.yaml`, `data/`, `logs/` bị gitignore theo `.gitignore`.
 
 ---
 
 ## 4. Luồng vận hành (Operational Logic)
 
 ### 4.1. Khởi động
 
 `main.py`:
 
 - Load config: `src.config_manager.load_config()`
 - Setup logging: `src.logger.setup_logging()`
 - Chạy theo mode:
   - `--run-now`: chạy 1 lần và exit
   - default: scheduler + dashboard
   - `--dashboard`: chỉ Flask
   - `--scheduler`: chỉ scheduler
 
 ### 4.2. Built-in run (Daily Check)
 
 `src.orchestrator.run_daily_check()`:
 
 - Tạo `run_id` theo format `YYYYMMDD_HHMMSS_<uuid6>`
 - Chạy song song 4 nhóm checks bằng `ThreadPoolExecutor`
 - Gom `List[CheckResult]`
 - Persist:
   - lưu từng `CheckResult` vào SQLite
   - update `RunSummary` (counts + overall_status)
 - Tạo HTML report và gửi thông báo
 - Lưu HTML report vào `data/reports/report_<run_id>.html`
 
 ### 4.3. Scheduler
 
 `src.scheduler.start_scheduler()`:
 
 - Tạo job `daily_check` theo `general.check_time` và `general.timezone`
 - `misfire_grace_time=300` (trễ tối đa 5 phút)
 - Chống overlap:
   - nếu `_is_running=True` thì bỏ qua trigger mới
 
 Ngoài ra scheduler còn:
 
 - Tự tạo job cho mỗi dynamic module enabled: `dynamic_module_<id>` chạy theo `module.daily_time` + `module.timezone`
 - Chống overlap theo từng module: bỏ qua nếu module đó đang chạy
 
 ---
 
 ## 5. Cấu hình
 
 ### 5.1. File config
 
 Mặc định: `config/config.yaml`.
 
 **Lưu ý:** file này không commit lên Git (`.gitignore`).
 
 Các nhóm cấu hình thường dùng:
 
 - `general.*`: time, timezone, max_workers, timeout, thư mục data...
 - `dashboard.*`: host/port/secret_key
 - `notifications.*`: email/webhook
 - `oracle.*`, `mongodb.*`, `hadoop.*`, `applications.*`: hosts + ssh settings
 - `security.encryption_key`: key mã hóa password cho dynamic modules
 
 ### 5.2. Ví dụ cấu hình tối thiểu (tham khảo)
 
 ```yaml
 general:
   check_time: "08:00"
   timezone: "Asia/Ho_Chi_Minh"
   max_workers: 10
   ssh_timeout: 30
   command_timeout: 60
   data_dir: "data"
 
 dashboard:
   host: "0.0.0.0"
   port: 5000
   debug: false
   secret_key: "CHANGE_ME_USE_RANDOM_STRING"
 
 security:
   encryption_key: "CHANGE_ME_DYNAMIC_MODULE_SECRET"
 
 notifications:
   email:
     enabled: true
     smtp_host: "smtp.company.com"
     smtp_port: 587
     smtp_use_tls: true
     smtp_use_ssl: false
     sender: "dailycheck@company.com"
     username: "dailycheck@company.com"
     password: "CHANGE_ME"
     smtp_require_auth: true
     recipients:
       - "ops@company.com"
   webhook:
     enabled: false
     url: "CHANGE_ME"
 ```
 
 ---
 
 ## 6. Cài đặt
 
 ### 6.1. Yêu cầu hệ thống
 
 - **OS:** Linux (Ubuntu 20.04+, CentOS 7+)
 - **Python:** 3.9+ (khuyến nghị 3.11)
 - **Network/Firewall:** máy chạy dailycheck SSH được đến các host target
 - **SSH:** khuyến nghị SSH key; vẫn hỗ trợ password
 
 ### 6.2. Cài đặt bằng virtualenv
 
 ```bash
 python3 -m venv venv
 source venv/bin/activate
 pip install -r requirements.txt
 ```
 
 ### 6.3. Chuẩn bị SSH
 
 Khuyến nghị SSH key:
 
 ```bash
 ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa
 ssh-copy-id -i ~/.ssh/id_rsa.pub user@<ip>
 ```
 
 Nếu dùng password:
 
 - Với built-in checks: cấu hình password trong config (tùy theo từng host/ssh defaults)
 - Với dynamic modules: password nhập qua dashboard và được mã hóa lưu vào SQLite
 
 ---
 
 ## 7. Chạy hệ thống (Usage)
 
 ### 7.1. Chạy 1 lần (manual)
 
 ```bash
 python main.py --run-now
 ```
 
 Exit code:
 
 - `0` nếu overall `OK`
 - `1` nếu `WARNING/CRITICAL/ERROR`
 
 ### 7.2. Chạy production (scheduler + dashboard)
 
 ```bash
 python main.py
 ```
 
 ### 7.3. Chạy riêng dashboard hoặc scheduler
 
 ```bash
 python main.py --dashboard
 python main.py --scheduler
 ```
 
 ### 7.4. Dùng config tùy chỉnh
 
 ```bash
 python main.py --config /path/to/config.yaml
 ```
 
 ---
 
## 8. Xác thực & Phân quyền (Authentication & RBAC)

### 8.1. Tổng quan

- **Model user**: lưu trong SQLite bảng `users` cùng database chính
- **Password hashing**: PBKDF2-HMAC-SHA256 (260,000 iterations) + salt ngẫu nhiên
- **Roles**: 2 vai trò
  - `admin`: toàn quyền (create/edit/delete modules, manage users)
  - `user`: chỉ được xem/edit/trigger các module do chính mình tạo
- **Session**: sử dụng Flask session (persistent, tuỳ cấu hình)

### 8.2. Mô hình dữ liệu User

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL DEFAULT '',
    role          TEXT    NOT NULL DEFAULT 'user',
    password_hash TEXT    NOT NULL,
    password_salt TEXT    NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 8.3. Cấu hình Auth

```yaml
auth:
  admin_username: "admin"
  admin_password: "admin123"        # CHANGE_ME in production
```

**Lưu ý:** Khi khởi động, nếu chưa có user nào, hệ thống tự tạo admin mặc định với username/password từ config.

### 8.4. API & Route Auth

- `GET /`: danh sách run → `@login_required`
- `GET /run/<run_id>`: chi tiết run → `@login_required`
- `GET /api/status`: healthcheck → không yêu cầu login
- `GET /modules`: danh sách modules → `@login_required`
- `POST /api/modules/<id>/edit`: sửa module → chỉ creator + admin
- `GET /dashboard/users`: manage users → `@admin_required`
- `POST /dashboard/users/create`: tạo user → `@admin_required`

### 8.5. Flow đăng nhập

```
1. Truy cập dashboard → redirect /login nếu chưa session
2. Nhập username + password
3. Gọi verify_password(username, password)
   - Kiểm tra user tồn tại + enabled
   - So sánh hash(input_password + stored_salt) == stored_hash
4. Nếu OK → login_user() → lưu session (user_id, username, role)
5. Redirect về trang trước đó (next param)
```

---

## 9. Dashboard & API

### 9.1. Truy cập

- Dashboard: `http://<host>:5000/` → cần login
- Healthcheck: `GET /api/status` → public

### 9.2. Các trang chính

- `/`: danh sách run gần đây + kết quả run mới nhất
- `/run/<run_id>`: chi tiết kết quả 1 lần chạy
- `/report/<run_id>`: xem HTML report đã lưu
- `/modules`: danh sách dynamic modules
- `/modules/new`: tạo dynamic module
- `/modules/<id>/edit`: sửa dynamic module
- `/users`: quản lý users (admin only)
- `/login`: đăng nhập
- `/logout`: đăng xuất

### 9.3. API endpoints chính

- `POST /api/trigger`: trigger built-in daily check
- `GET /api/status`: trạng thái (running + latest run)
- `GET /api/runs`: danh sách runs
- `GET /api/run/<run_id>`: chi tiết results
- `GET/POST /api/modules`: list / create dynamic module
- `GET/PUT/DELETE /api/modules/<id>`: read/update/delete module
- `POST /api/modules/<id>/trigger`: trigger module ngay
- `GET /api/modules/<id>/runs`: lịch sử run của module
- `GET /api/users`: danh sách users (admin)
- `POST /api/users`: tạo user (admin)
- `PUT /api/users/<id>`: sửa user (admin)
- `DELETE /api/users/<id>`: xóa user (admin)
 
 ---
 
## 10. Dynamic Modules
 
 - **Hosts**: ip/port/username, auth type (`password`/`key`), variables
 - **Steps**: tên bước, command, timeout, chạy trên từng host hay chỉ host đầu tiên, rule đánh giá status
 - **Schedule**: timezone + `daily_time` (HH:MM)
 - **Recipients**: danh sách email nhận report của module
 
### 10.1. Templates

`src.dynamic_modules.MODULE_TEMPLATES` cung cấp template cho:

- `oracle`
- `mongodb`
- `hadoop`
- `application`
- `custom`

Dashboard có API `GET /api/module-templates` và `GET /api/module-templates/<template_key>`.

### 10.2. Command template variables
 
 Dynamic module steps cho phép dùng `{{var}}` trong command. Context hỗ trợ:
 
 - `{{host_name}}`, `{{host_ip}}`, `{{module_name}}`, `{{system_name}}`
 - Các biến khai báo ở `host.variables` (vd `instance_name`, `service_name`...)
 
 Hệ thống có validate để giảm rủi ro:
 
 - Cấm `sudo`, `su`, command substitution `$(` và backticks
 - Cấm một số ký tự nguy hiểm trong giá trị biến
 - Nếu thiếu biến trong template sẽ trả lỗi
 
 ---
 
## 11. Dữ liệu, báo cáo, logs

- **SQLite**: `data/dailycheck.db` (chứa check results, dynamic modules, users)
- **HTML reports**: `data/reports/report_<run_id>.html`
- **Logs**: `logs/` (tên file log phụ thuộc `setup_logging()`)

Dashboard có thể xem report qua route `/report/<run_id>`.

---

## 12. Docker
 
### 12.1. Chạy với Docker Compose

```bash
docker-compose up --build -d
docker-compose logs -f
```

Mặc định container chạy `python main.py` (scheduler + dashboard).

### 12.2. Volumes/Mounts quan trọng

- `./data:/app/data`: persist database + reports
- `./logs:/app/logs`: persist logs
- `${HOME}/.ssh:/root/.ssh:ro`: SSH keys để kết nối servers
- `./config/config.yaml:/app/config/config.yaml:ro`: config runtime

### 12.3. Healthcheck

Docker compose healthcheck gọi:

- `http://localhost:5000/api/status`

---

## 14. Testing

Unit tests nằm trong `tests/` (bao gồm auth, validation, dynamic modules).

```bash
python3 -m unittest discover -s tests -v
```

Manual verification:

```bash
python main.py --run-now
```

---

## 15. Xử lý sự cố

### 15.1. SSH không kết nối được

- Kiểm tra từ máy chạy dailycheck:

```bash
ssh -i ~/.ssh/id_rsa user@<ip>
```

- Kiểm tra port/firewall
- Nếu chạy Docker: kiểm tra volume `${HOME}/.ssh` đã mount đúng

### 15.2. Dashboard không lên

- Kiểm tra port `5000` đang listen
- Kiểm tra cấu hình `dashboard.host`, `dashboard.port`
- Xem logs trong `logs/` hoặc `docker-compose logs -f`

### 15.3. Không thể đăng nhập

- Kiểm tra user được enable: `enabled = 1` trong DB
- Kiểm tra username (case-insensitive search)
- Xác nhận password đúng (so sánh hash)
- Nếu quên mật khẩu admin: cập nhật hash trực tiếp hoặc xóa user + restart để tạo admin mới

### 15.4. Dynamic module chạy lỗi "Missing template variable"

- Đảm bảo step có dùng `{{var}}` thì host phải có `variables[var]`.

### 15.5. Không gửi được email/webhook

- Kiểm tra `notifications.email.*`
- Kiểm tra network đến SMTP
- Kiểm tra credentials

---

## 15. Xử lý sự cố
 
 ## 14. Xử lý sự cố
 
 ### 14.1. SSH không kết nối được
 
 - Kiểm tra từ máy chạy dailycheck:
 
 ```bash
 ssh -i ~/.ssh/id_rsa user@<ip>
 ```
 
 - Kiểm tra port/firewall
 - Nếu chạy Docker: kiểm tra volume `${HOME}/.ssh` đã mount đúng
 
 ### 14.2. Dashboard không lên
 
 - Kiểm tra port `5000` đang listen
 - Kiểm tra cấu hình `dashboard.host`, `dashboard.port`
 - Xem logs trong `logs/` hoặc `docker-compose logs -f`
 
 ### 14.3. Dynamic module chạy lỗi “Missing template variable”
 
 - Đảm bảo step có dùng `{{var}}` thì host phải có `variables[var]`.
 
 ### 14.4. Không gửi được email/webhook
 
 - Kiểm tra `notifications.email.*`
 - Kiểm tra network đến SMTP
 - Kiểm tra credentials
 
 ---
 
 ## 15. Tình trạng
 
 Tình trạng: **Đã cập nhật README thành tài liệu mô tả project chi tiết** (công nghệ, cấu trúc, luồng vận hành, cài đặt, usage, Docker, dynamic modules, bảo mật, troubleshooting).

-------------------------------------------------------------

docker pull python:3.11-slim
docker save python:3.11-slim -o python311.tar
docker save simo-dailycheck:1.0 -o simo-dailycheck:1.0.tar
docker load -i python311.tar
docker run -d -p 5000:5000 simo-dailycheck:1.0
