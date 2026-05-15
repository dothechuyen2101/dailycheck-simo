# Hệ thống SIMO Daily Check – Hướng dẫn sử dụng

## Mục lục

1. [Tổng quan](#tổng-quan)
2. [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
3. [Khởi động nhanh](#khởi-động-nhanh)
4. [Cài đặt](#cài-đặt)
5. [Cấu hình](#cấu-hình)
6. [Chạy hệ thống](#chạy-hệ-thống)
7. [Hướng dẫn Dashboard](#hướng-dẫn-dashboard)
8. [Quản lý Dynamic Modules](#quản-lý-dynamic-modules)
9. [Báo cáo và kết quả](#báo-cáo-và-kết-quả)
10. [Xử lý sự cố](#xử-lý-sự-cố)
11. [Best Practices](#best-practices)

---

## Tổng quan

**SIMO Daily Check** là hệ thống giám sát tự động hàng ngày cho các thành phần cơ sở hạ tầng quan trọng:

- **Oracle RAC** – Tình trạng instance cơ sở dữ liệu, tablespace, ASM, CRS
- **MongoDB** – Trạng thái dịch vụ, replication, sử dụng tài nguyên
- **Hadoop** – Sức khỏe HDFS, trạng thái datanode, dung lượng lưu trữ
- **Application** – Kiểm tra sức khỏe ứng dụng tùy chỉnh trên các máy chủ được chỉ định
- **Dynamic Modules** – Kiểm tra tùy chỉnh do người dùng xác định với các bước SSH linh hoạt

### Tính năng chính

✅ **Lên lịch tự động** – Kiểm tra hàng ngày vào các thời điểm được cấu hình  
✅ **Thực thi song song** – Nhiều kiểm tra chạy cùng lúc để nâng hiệu suất  
✅ **Web Dashboard** – Giám sát thực tế và báo cáo lịch sử  
✅ **Dynamic Modules** – Tạo kiểm tra tùy chỉnh mà không cần thay đổi mã  
✅ **Hệ thống cảnh báo** – Thông báo email và webhook cho các vấn đề  
✅ **Lưu trữ được mã hóa** – Lưu trữ mật khẩu SSH an toàn  
✅ **Sẵn sàng Docker** – Thiết lập Docker Compose được cấu hình sẵn  

### Logic trạng thái tổng thể

Hệ thống tạo **Trạng thái tổng thể** dựa trên tất cả kết quả kiểm tra:

| Trạng thái | Điều kiện |
|--------|-----------|
| 🔴 **CRITICAL** | Có ít nhất một kiểm tra có trạng thái CRITICAL hoặc ERROR |
| 🟡 **WARNING** | Có ít nhất một kiểm tra có trạng thái WARNING (không có CRITICAL/ERROR) |
| 🟢 **OK** | Tất cả kiểm tra đều thành công |

---

## Yêu cầu hệ thống

### Phần cứng
- **CPU:** Khuyến nghị 2+ nhân
- **Bộ nhớ:** Tối thiểu 4GB, khuyến nghị 8GB
- **Lưu trữ:** 5GB cho nhật ký và báo cáo
- **Mạng:** Truy cập SSH trực tiếp vào cơ sở hạ tầng được giám sát

### Phần mềm
- **OS:** Linux (Ubuntu 20.04+, CentOS 7+, RHEL 7+)
- **Python:** 3.9 hoặc cao hơn
- **SSH:** Được cấu hình xác thực dựa trên khóa hoặc mật khẩu
- **Docker (Tuỳ chọn):** Để triển khai được containerize

### Truy cập mạng
- SSH cổng 22 (hoặc cổng SSH được cấu hình) mở cho tất cả các máy chủ được giám sát
- Để cảnh báo email: Máy chủ SMTP có thể truy cập
- Cho webhook: Truy cập internet vào các endpoint webhook

### Hệ thống được giám sát

| Hệ thống | Tối thiểu Node | Yêu cầu |
|--------|---------------|--------------|
| Oracle RAC | 2 | ORACLE_HOME có thể truy cập, sqlplus có sẵn |
| MongoDB | 2 | Dịch vụ MongoDB có thể truy cập |
| Hadoop | 3 | HDFS dfsadmin có thể truy cập |
| Application | 1+ | Endpoint ứng dụng tùy chỉnh có thể truy cập |

---

## Khởi động nhanh

Đối với người dùng lần đầu tiên, hãy làm theo các bước này để hoạt động ngay lập tức:

```bash
# 1. Điều hướng đến thư mục dự án
cd simo_dailycheck

# 2. Tạo môi trường ảo Python
python3 -m venv venv
source venv/bin/activate  # Trên Windows: venv\Scripts\activate

# 3. Cài đặt các gói phụ thuộc
pip install -r requirements.txt

# 4. Thiết lập khóa SSH (hoặc cấu hình mật khẩu trong config.yaml)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa
ssh-copy-id -i ~/.ssh/id_rsa.pub user@server-ip

# 5. Chỉnh sửa cấu hình
nano config/config.yaml  # Cập nhật chi tiết cơ sở hạ tầng của bạn

# 6. Kiểm tra hệ thống
python main.py --run-now

# 7. Khởi động hệ thống đầy đủ (scheduler + dashboard)
python main.py
```

**Điều gì xảy ra khi bạn chạy `--run-now`:**
- Tất cả kiểm tra được cấu hình thực hiện ngay lập tức
- Kết quả được lưu vào cơ sở dữ liệu
- Một báo cáo được tạo
- Chương trình thoát

---

## Cài đặt

### 1. Thiết lập các điều kiện tiên quyết

#### Ubuntu/Debian

```bash
# Cập nhật trình quản lý gói
sudo apt update

# Cài đặt Python và các gói phụ thuộc hệ thống
sudo apt install -y python3.9 python3-pip python3-venv
sudo apt install -y openssh-client  # Cho kết nối SSH
```

#### CentOS/RHEL

```bash
# Cài đặt Python và các gói phụ thuộc
sudo yum install -y python39 python39-pip python39-devel
sudo yum install -y openssh-clients
```

### 2. Clone/Tải xuống Dự án

```bash
# Clone từ repository (nếu sử dụng Git)
git clone <repository-url> simo_dailycheck
cd simo_dailycheck

# Hoặc tải xuống và giải nén
tar -xzf simo_dailycheck.tar.gz
cd simo_dailycheck
```

### 3. Tạo Môi trường ảo

```bash
# Tạo môi trường ảo
python3 -m venv venv

# Kích hoạt nó
source venv/bin/activate

# Xác minh kích hoạt (dấu nhắc sẽ hiển thị (venv))
python --version  # Nên hiển thị Python 3.9+
```

### 4. Cài đặt các gói phụ thuộc

```bash
# Cài đặt gói Python
pip install --upgrade pip
pip install -r requirements.txt

# Xác minh cài đặt
python -c "import flask, apscheduler; print('OK')"
```

### 5. Thiết lập xác thực SSH

**Khuyến nghị: Xác thực dựa trên khóa SSH**

```bash
# Tạo khóa SSH (nếu chưa thực hiện)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N ""

# Sao chép khóa công khai cho mỗi máy chủ được giám sát
for server in oracle-1 oracle-2 mongo-1 mongo-2 hadoop-1 hadoop-2 hadoop-3; do
    ssh-copy-id -i ~/.ssh/id_rsa.pub user@$server
done

# Kiểm tra kết nối
ssh user@oracle-1 "echo 'Connection successful'"
```

**Thay thế: Xác thực dựa trên mật khẩu**

Nếu bạn thích mật khẩu, chúng có thể được lưu trong tệp cấu hình (xem phần Cấu hình bên dưới).

### 6. Cấu hình ban đầu

```bash
# Tạo sao lưu cấu hình mặc định
cp config/config.yaml config/config.yaml.backup

# Chỉnh sửa cấu hình (xem phần Cấu hình bên dưới)
nano config/config.yaml
```

### 7. Xác minh cài đặt

```bash
# Chạy một kiểm tra duy nhất để xác minh thiết lập
python main.py --run-now

# Nếu thành công, bạn sẽ thấy:
# - Kết quả kiểm tra trong bảng điều khiển
# - Một báo cáo được tạo trong data/reports/
# - Mục nhập cơ sở dữ liệu trong data/dailycheck.db
```

### Docker Installation (Tuỳ chọn)

```bash
# Xây dựng Docker image
docker-compose build

# Chạy với Docker
docker-compose up

# Truy cập dashboard tại http://localhost:5000
```

---

## Cấu hình

### Vị trí tệp cấu hình

`config/config.yaml` – Tệp cấu hình chính (không commit với mật khẩu thực vào các kho lưu trữ Git công cộng)

### Các phần cấu hình cần thiết

#### 1. Cài đặt chung

```yaml
general:
  check_time: "08:00"        # Thời gian chạy kiểm tra hàng ngày (HH:MM, định dạng 24 giờ)
  prep_time: "07:55"         # Thời gian làm ấm các kết nối SSH
  report_time: "08:15"       # Thời gian gửi báo cáo email
  timezone: "Asia/Ho_Chi_Minh"  # Múi giờ của bạn
  max_workers: 10            # Số luồng song song (kiểm tra hệ thống)
  ssh_timeout: 30            # Timeout kết nối SSH (giây)
  command_timeout: 60        # Timeout thực thi lệnh (giây)
  data_dir: "data"           # Thư mục cho báo cáo và cơ sở dữ liệu
  log_dir: "logs"            # Thư mục cho nhật ký
  db_path: "data/dailycheck.db"  # Đường dẫn cơ sở dữ liệu SQLite
```

#### 2. Cấu hình SSH

```yaml
ssh_defaults:
  port: 22                   # Cổng SSH (thường là 22)
  username: "root"           # Người dùng SSH mặc định
  password: "CHANGE_ME"      # Mật khẩu SSH mặc định (sử dụng khóa nếu có thể)
  key_path: "~/.ssh/id_rsa"  # Đường dẫn đến khóa SSH riêng tư
```

#### 3. Cài đặt Dashboard

```yaml
dashboard:
  host: "0.0.0.0"            # Ràng buộc với tất cả giao diện (0.0.0.0) hoặc IP cụ thể
  port: 5000                 # Cổng web (mặc định 5000)
  secret_key: "change-me-to-random-string"  # Khóa bí mật Flask
```

#### 4. Cấu hình cảnh báo

```yaml
alert:
  email:
    enabled: true
    smtp_server: "mail.example.com"
    smtp_port: 587
    sender: "simo-alerts@example.com"
    recipients:
      - "ops-team@example.com"
      - "dba-team@example.com"
    use_tls: true
    username: "smtp-user"
    password: "smtp-password"
  
  webhook:
    enabled: false
    url: "https://hooks.example.com/simo"
```

#### 5. Cấu hình Oracle

```yaml
oracle:
  db_name: "SIMO"            # Tên cơ sở dữ liệu
  version: "19.3"            # Phiên bản Oracle
  hosts:
    - name: "DB1"            # Tên nút
      ip: "192.168.1.10"     # Địa chỉ IP
      instance: "simo1"      # Tên instance
      ssh:
        username: "oracle"
        password: "CHANGE_ME"
        key_path: "~/.ssh/id_rsa"
    
    - name: "DB2"
      ip: "192.168.1.11"
      instance: "simo2"
      ssh:
        username: "oracle"
```

**Các tùy chọn Oracle cụ thể:**

```yaml
  # (Tuỳ chọn) Kết nối cơ sở dữ liệu trực tiếp sử dụng cx_Oracle
  db_connection:
    dsn: "192.168.1.10:1521/SIMO"
    username: "mon_user"     # Người dùng có SELECT trên chế độ xem DBA_*
    password: "CHANGE_ME"
  
  # Dịch vụ CRS được phép ngoại tuyến (RAC cụ thể)
  crs_allowed_offline:
    - "ora.gsd"
    - "ora.oc4j"
```

#### 6. Cấu hình MongoDB

```yaml
mongodb:
  db_name: "admin"           # Cơ sở dữ liệu quản trị
  hosts:
    - name: "Mongo1"
      ip: "192.168.1.20"
      ssh:
        username: "root"
        password: "CHANGE_ME"
    
    - name: "Mongo2"
      ip: "192.168.1.21"
      ssh:
        username: "root"
  
  service_name: "mongod"     # Tên dịch vụ cho systemctl
  has_docker: false          # true nếu MongoDB chạy trong Docker
```

#### 7. Cấu hình Hadoop

```yaml
hadoop:
  db_name: "hadoop"
  nodes:
    - name: "NameNode"
      ip: "192.168.1.30"
      ssh:
        username: "hadoop"
    
    - name: "DataNode1"
      ip: "192.168.1.31"
      ssh:
        username: "hadoop"
    
    - name: "DataNode2"
      ip: "192.168.1.32"
      ssh:
        username: "hadoop"
  
  hdfs_port: 8020            # Cổng HDFS
  rack_awareness: true       # Kiểm tra tôpô rack
```

#### 8. Cấu hình ứng dụng

```yaml
application:
  apps:
    - name: "MyApp"
      type: "http"           # http, tcp, hoặc tùy chỉnh
      hosts:
        - name: "AppServer1"
          ip: "192.168.1.40"
          port: 8080
          ssh:
            username: "appuser"
      
      checks:
        - type: "http_health"
          endpoint: "/health"
          expected_status: 200
        
        - type: "disk_usage"
          path: "/opt/myapp"
          warning_threshold: 80
          critical_threshold: 90
```

### Best Practices cấu hình

1. **Bảo mật trước tiên:**
   - Sử dụng khóa SSH thay vì mật khẩu khi có thể
   - Không bao giờ commit `config.yaml` với mật khẩu thực vào Git
   - Đặt quyền tệp hạn chế: `chmod 600 config/config.yaml`
   - Sử dụng mục nhập `.gitignore` cho tệp cấu hình

2. **Thời gian:**
   - Đặt `check_time` trước giờ kinh doanh hoặc thời gian không cao điểm
   - Cách xa nhau ít nhất 5 phút `prep_time` và `check_time`
   - Đảm bảo `report_time` sau `check_time`

3. **Tài nguyên:**
   - Điều chỉnh `max_workers` dựa trên dung lượng hệ thống
   - Giảm giá trị (4-6) cho các hệ thống có tài nguyên hạn chế
   - Giá trị cao hơn (10-15) cho cơ sở hạ tầng lớn với nhiều máy chủ

4. **Xử lý lỗi:**
   - Đặt timeout phù hợp cho mạng của bạn (thường 30-60 giây)
   - Thêm thời gian đệm cho các kiểm tra cơ sở dữ liệu lớn

---

## Chạy hệ thống

### Tùy chọn dòng lệnh

```bash
# Hiển thị trợ giúp
python main.py --help

# Mặc định: Chạy scheduler + dashboard
python main.py

# Chạy kiểm tra ngay lập tức và thoát
python main.py --run-now

# Chỉ chạy scheduler (chỉ kiểm tra nền)
python main.py --scheduler

# Chỉ chạy dashboard (không kiểm tra tự động)
python main.py --dashboard

# Sử dụng tệp cấu hình tùy chỉnh
python main.py --config /path/to/custom/config.yaml

# Kết hợp các tùy chọn
python main.py --run-now --config config/prod.yaml
```

### Giải thích các chế độ chạy

#### Chế độ 1: Hệ thống đầy đủ (Mặc định)

```bash
python main.py
```

- **Khởi động:** Scheduler + Dashboard
- **Hành vi:**
  - Scheduler chạy kiểm tra hàng ngày tự động vào giờ được cấu hình
  - Dashboard có thể truy cập tại `http://localhost:5000`
  - Bạn có thể kích hoạt thủ công kiểm tra từ dashboard bất kỳ lúc nào
  - Chạy vô thời hạn cho đến khi dừng (Ctrl+C)

**Sử dụng khi:** Bạn muốn giám sát đầy đủ với giao diện web

#### Chế độ 2: Chạy một lần & Thoát

```bash
python main.py --run-now
```

- **Chạy:** Tất cả kiểm tra ngay lập tức
- **Thoát:** Ngay sau hoàn thành
- **Đầu ra:** Đầu ra bảng điều khiển + tạo báo cáo
- **Trường hợp sử dụng:** Kiểm tra, tích hợp CI/CD, thực thi công việc cron

**Sử dụng khi:** Kiểm tra cấu hình, chạy trong tập lệnh hoặc đường ống CI/CD

#### Chế độ 3: Chỉ Scheduler

```bash
python main.py --scheduler
```

- **Chạy:** Scheduler nền cho kiểm tra hàng ngày tự động
- **Không:** Web dashboard
- **Hữu ích:** Máy chủ không giao diện hoặc khi dashboard chạy ở nơi khác

**Sử dụng khi:** Chạy trên máy chủ không giao diện mà không có truy cập web

#### Chế độ 4: Chỉ Dashboard

```bash
python main.py --dashboard
```

- **Chạy:** Web dashboard chỉ
- **Không:** Kiểm tra hàng ngày tự động
- **Kích hoạt thủ công:** Có sẵn từ dashboard

**Sử dụng khi:** Dashboard và kiểm tra chạy trên các máy chủ riêng biệt

### Chạy như một dịch vụ nền

#### Sử dụng `nohup`

```bash
# Chạy ở nền, đầu ra vào tệp nhật ký
nohup python main.py > simo.log 2>&1 &

# Giết tiến trình sau này
pkill -f "python main.py"
```

#### Sử dụng phiên `screen`

```bash
# Bắt đầu phiên screen
screen -S simo

# Bên trong screen: chạy hệ thống
python main.py

# Tách: Ctrl+A rồi D

# Kết nối lại sau
screen -r simo
```

#### Sử dụng dịch vụ `systemd` (Khuyến nghị)

Tạo `/etc/systemd/system/simo-dailycheck.service`:

```ini
[Unit]
Description=SIMO Daily Check System
After=network.target

[Service]
Type=simple
User=simo
WorkingDirectory=/opt/simo_dailycheck
Environment="PATH=/opt/simo_dailycheck/venv/bin"
ExecStart=/opt/simo_dailycheck/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Khi đó:

```bash
# Bật và khởi động dịch vụ
sudo systemctl enable simo-dailycheck
sudo systemctl start simo-dailycheck

# Kiểm tra trạng thái
sudo systemctl status simo-dailycheck

# Xem nhật ký
sudo journalctl -u simo-dailycheck -f
```

#### Sử dụng `cron` cho các lần chạy theo lịch

Cho chỉ chạy kiểm tra (không dashboard):

```bash
# Chỉnh sửa crontab
crontab -e

# Thêm dòng để chạy kiểm tra lúc 8 sáng mỗi ngày làm việc
0 8 * * 1-5 cd /home/simo/dailycheck_simo && /home/simo/dailycheck_simo/venv/bin/python main.py --run-now >> /var/log/simo-cron.log 2>&1
```

---

## Hướng dẫn Dashboard

### Truy cập Dashboard

1. **Khởi động hệ thống:**
   ```bash
   python main.py
   ```

2. **Mở trong trình duyệt:**
   - Cục bộ: `http://localhost:5000`
   - Từ xa: `http://server-ip:5000`

3. **Mặc định:** Không cần xác thực (cấu hình nếu cần)

### Tính năng Dashboard

#### 1. Trang chủ / Trang trạng thái

**URL:** `/`

- **Hiển thị trạng thái tổng thể:** Hiển thị trạng thái tổng thể hiện tại (OK, WARNING, CRITICAL)
- **Kết quả lần chạy mới nhất:** Tóm tắt lần thực thi kiểm tra gần nhất
- **Thống kê nhanh:**
  - Tổng số kiểm tra được chạy
  - Số lượng kết quả CRITICAL/WARNING/OK
  - Thời gian kiểm tra cuối cùng
  - Thời gian kiểm tra được lên lịch tiếp theo

#### 2. Chế độ xem kết quả kiểm tra

**URL:** `/` (cuộn xuống) hoặc điều hướng từ trang chủ

**Hiển thị:**
- Kết quả kiểm tra riêng lẻ cho mỗi hệ thống
- Huy hiệu trạng thái (🟢 OK, 🟡 WARNING, 🔴 CRITICAL)
- Dấu thời gian kiểm tra
- Chi tiết/thông báo cho mỗi kết quả

**Có thể lọc theo:**
- Loại hệ thống (Oracle, MongoDB, Hadoop, Application)
- Mức độ trạng thái
- Phạm vi ngày

#### 3. Chế độ xem báo cáo

**URL:** `/reports` hoặc `/runs`

**Tính năng:**
- Danh sách tất cả các báo cáo được tạo
- Mỗi báo cáo hiển thị:
  - ID chạy (dấu thời gian)
  - Trạng thái tổng thể
  - Số lượng kết quả
  - Thời gian tạo
  - Tùy chọn tải xuống

**Hành động:**
- Nhấp vào báo cáo để xem báo cáo HTML chi tiết
- Tải xuống báo cáo dưới dạng HTML
- Xuất ra CSV (nếu có sẵn)

#### 4. Quản lý Dynamic Modules

**URL:** `/modules`

**Xem tất cả dynamic modules:**
- Tên và mô tả mô-đun
- Trạng thái bật/tắt
- Kết quả lần chạy cuối cùng
- Thời gian lần chạy cuối cùng

**Hành động mô-đun:**
- Tạo mô-đun mới: `/modules/new`
- Chỉnh sửa mô-đun: `/modules/<id>/edit`
- Xem các lần chạy: `/modules/<id>/runs`
- Kích hoạt chạy thủ công: `/modules/<id>/trigger`

#### 5. Tạo/Chỉnh sửa Dynamic Module

**URL:** `/modules/new` (tạo) hoặc `/modules/<id>/edit` (chỉnh sửa)

**Các trường biểu mẫu:**
- **Name:** Tên mô-đun (bắt buộc)
- **Description:** Mô-đun kiểm tra cái gì
- **Enabled:** Hộp kiểm để bật/tắt
- **Schedule:** Biểu thức Cron để lên lịch
- **Hosts:** Chọn máy chủ nào để chạy
- **Steps:** Xác định lệnh SSH để thực thi

**Cấu hình bước:**
Cho mỗi bước:
- **Command:** Lệnh SSH để thực thi
- **Expected Output:** Từ khóa để tìm kiếm (tiêu chí thành công)
- **Error Keywords:** Từ khóa cho biết thất bại
- **Warning Keywords:** Từ khóa cho biết cảnh báo

#### 6. Điểm cuối API

**Kiểm tra sức khỏe:**
```
GET /api/status
Trả về: {"status": "ok", "last_check": "2026-05-12 14:30:00"}
```

**Lấy kết quả mới nhất:**
```
GET /api/results
Trả về: Danh sách kết quả kiểm tra mới nhất
```

**Lấy tất cả Dynamic Modules:**
```
GET /api/modules
Trả về: Danh sách dynamic modules
```

**Lấy chi tiết mô-đun:**
```
GET /api/modules/<id>
Trả về: Cấu hình mô-đun và các lần chạy cuối cùng
```

**Kích hoạt chạy mô-đun:**
```
POST /api/modules/<id>/trigger
Trả về: Kết quả thực thi
```

**Lấy lịch sử chạy mô-đun:**
```
GET /api/modules/<id>/runs
Trả về: Danh sách các lần chạy gần đây với kết quả
```

---

## Quản lý Dynamic Modules

### Dynamic Modules là gì?

Dynamic modules cho phép bạn xác định kiểm tra tùy chỉnh mà không cần sửa đổi mã. Mỗi mô-đun:
- Chạy theo lịch bạn xác định
- Thực thi lệnh SSH trên các máy chủ được chọn
- Đánh giá kết quả dựa trên mẫu đầu ra
- Lưu trữ lịch sử trong cơ sở dữ liệu

### Tạo Dynamic Module

#### Bước 1: Điều hướng đến trang Modules

1. Mở dashboard
2. Nhấp vào "Modules" trong điều hướng
3. Nhấp vào nút "Create New Module"

#### Bước 2: Cấu hình các cơ bản mô-đun

**Các trường biểu mẫu:**

| Trường | Mô tả | Ví dụ |
|-------|----------|---------|
| **Name** | Định danh mô-đun | `MySQL_Replication_Check` |
| **Description** | Mô-đun kiểm tra cái gì | "Xác minh độ trễ sao chép MySQL" |
| **Enabled** | Hộp kiểm để bật | ✓ Được kiểm tra |
| **Schedule** | Biểu thức Cron | `0 */4 * * *` (mỗi 4 giờ) |

**Biểu thức Cron phổ biến:**
- `0 8 * * 1-5` – 8 sáng vào các ngày làm việc
- `0 */6 * * *` – Mỗi 6 giờ
- `0 12 * * *` – Hàng ngày lúc giữa trưa
- `*/30 * * * *` – Mỗi 30 phút
- `0 0 * * 0` – Hàng tuần vào Chủ nhật

#### Bước 3: Chọn máy chủ

Chọn máy chủ nào mô-đun này sẽ chạy:
- Máy chủ khả dụng được liệt kê từ cấu hình
- Đã bật lựa chọn đa
- Ít nhất một máy chủ được yêu cầu

#### Bước 4: Xác định các bước

Cho mỗi bước kiểm tra, xác định:

**Ví dụ: Kiểm tra trạng thái sao chép MySQL**

| # | Lệnh | Đầu ra dự kiến | Từ khóa lỗi | Từ khóa cảnh báo |
|---|------|-----------------|-----------------|------------------|
| 1 | `SHOW SLAVE STATUS\G` | `Seconds_Behind_Master` | `ERROR`, `Failed` | `Lag > 10` |
| 2 | `SELECT version()` | `8.0` | | |

**Logic đánh giá quy tắc:**

1. Lệnh thực thi qua SSH
2. Kiểm tra đầu ra cho từ khóa **Đầu ra dự kiến**:
   - Tìm thấy → Kiểm tra **Từ khóa lỗi**
   - Không tìm thấy → Kiểm tra **Từ khóa cảnh báo**
3. Trạng thái kết quả:
   - Tìm thấy từ khóa lỗi → 🔴 CRITICAL
   - Tìm thấy từ khóa cảnh báo → 🟡 WARNING  
   - Tìm thấy đầu ra dự kiến, không lỗi → 🟢 OK
   - Đầu ra không dự kiến → 🟡 WARNING

#### Bước 5: Lưu & Kiểm tra

1. Nhấp vào "Save Module"
2. Mô-đun xuất hiện trong danh sách mô-đun
3. Nhấp vào "Run Now" để kiểm tra ngay lập tức
4. Kiểm tra kết quả trong lịch sử chạy

### Quản lý các mô-đun hiện có

#### Xem chi tiết mô-đun

1. Đi đến trang Modules
2. Nhấp vào tên mô-đun
3. Xem:
   - Cấu hình
   - 10 lần chạy gần đây với dấu thời gian và kết quả
   - Xu hướng trạng thái tổng thể

#### Chỉnh sửa mô-đun

1. Nhấp vào nút "Edit" trên thẻ mô-đun
2. Sửa đổi cấu hình
3. Lưu thay đổi
4. Cập nhật lịch có hiệu lực cho lần chạy tiếp theo

#### Bật/Tắt mô-đun

1. Nhấp vào bật tắt trên thẻ mô-đun
2. Mô-đun dừng/bắt đầu chạy theo lịch
3. Kích hoạt thủ công vẫn hoạt động

#### Xóa mô-đun

1. Nhấp vào nút "Delete"
2. Xác nhận xóa
3. Mô-đun và tất cả lịch sử được xóa

### Ví dụ về mô-đun

#### Ví dụ 1: Kiểm tra sức khỏe dịch vụ

```
Name: Redis_Service_Check
Description: Xác minh Redis đang chạy và phản hồi
Enabled: ✓
Schedule: 0 */2 * * * (mỗi 2 giờ)
Hosts: redis-1, redis-2

Steps:
1. Command: systemctl is-active redis
   Expected: active
   Error: inactive
   
2. Command: redis-cli ping
   Expected: PONG
   Error: NOAUTH, error
```

#### Ví dụ 2: Theo dõi không gian đĩa

```
Name: Disk_Usage_Monitor
Description: Kiểm tra sử dụng đĩa trên máy chủ ứng dụng
Enabled: ✓
Schedule: 0 12 * * * (hàng ngày lúc giữa trưa)
Hosts: app-server-1, app-server-2

Steps:
1. Command: df -h / | tail -1 | awk '{print $5}'
   Expected: 70
   Warning: 80
   Error: 90
```

#### Ví dụ 3: Trạng thái sao lưu cơ sở dữ liệu

```
Name: Backup_Completion_Check
Description: Xác minh sao lưu ban đêm đã hoàn thành
Enabled: ✓
Schedule: 0 7 * * * (hàng ngày lúc 7 sáng)
Hosts: backup-server

Steps:
1. Command: ls -lt /backups/daily | head -1 | awk '{print $6"-"$7"-"$8}'
   Expected: ngày hôm nay
   Error: hôm qua, cũ hơn
   Warning: đang chờ xử lý
```

---

## Báo cáo và kết quả

### Các loại báo cáo

#### 1. Báo cáo hàng ngày tự động

Được tạo tự động vào lúc `report_time` được cấu hình:
- **Định dạng:** Email HTML + tệp được lưu
- **Người nhận:** Danh sách email được cấu hình
- **Nội dung:**
  - Trạng thái hệ thống tổng thể
  - Kết quả cho mỗi hệ thống (Oracle, MongoDB, Hadoop, Application)
  - Tóm tắt Critical/Warning
  - Dấu thời gian và thời gian chạy

#### 2. Báo cáo theo yêu cầu

Được tạo khi bạn nhấp vào "Generate Report" trên dashboard:
- Định dạng giống báo cáo tự động
- Có thể được tạo bất cứ lúc nào
- Có sẵn để tải xuống từ trang báo cáo

#### 3. Báo cáo Dynamic Module

Được tạo sau mỗi lần chạy mô-đun:
- Nhật ký thực thi từng bước
- Lệnh và đầu ra cho mỗi bước
- Logic xác định trạng thái
- Dấu thời gian và thời gian thực thi

### Xem báo cáo

#### Trong Dashboard

1. **Điều hướng đến Báo cáo:** Nhấp vào "Reports" trong menu điều hướng
2. **Chọn báo cáo:** Nhấp vào báo cáo từ danh sách
3. **Xem chi tiết:** Xem báo cáo HTML đầy đủ trong modal hoặc tab mới
4. **Tải xuống:** Nhấp vào nút tải xuống để lưu dưới dạng tệp

#### Vị trí tệp

Báo cáo được lưu vào: `data/reports/report_<timestamp>_<id>.html`

Ví dụ: `data/reports/report_20260512_140725_4a1028.html`

#### Nội dung báo cáo

Báo cáo điển hình bao gồm:

```
SIMO Daily Check – Báo cáo hàng ngày
Được tạo: 2026-05-12 14:30:00
Trạng thái tổng thể: ✓ OK

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ORACLE RAC
─────────
✓ Trạng thái Instance DB1: OK
✓ Trạng thái Instance DB2: OK
✓ Sử dụng Tablespace: OK (tất cả dưới ngưỡng)
⚠ Nhóm đĩa ASM: WARNING (87% được sử dụng)
... (11 kiểm tra tổng cộng)

MONGODB
───────
✓ Dịch vụ Mongo1: Chạy
✓ Dịch vụ Mongo2: Chạy
✓ Trạng thái sao chép: OK
... (3 kiểm tra cho mỗi máy chủ)

HADOOP
──────
✓ Sức khỏe NameNode: OK
✓ DataNodes: 3 trực tuyến, 0 ngoại tuyến
✓ Sử dụng HDFS: 62% (OK)
... (3 kiểm tra cho mỗi nút)

APPLICATION
────────────
✓ MyApp - AppServer1: Lành mạnh
✓ MyApp - AppServer2: Lành mạnh
⚠ MyApp - AppServer3: Suy giảm (thời gian phản hồi cao)
... (cho mỗi ứng dụng và máy chủ được cấu hình)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TÓM TẮT
───────
Tổng kiểm tra: 47
✓ OK: 44
⚠ WARNING: 2
✗ CRITICAL: 0

Thời gian thực thi: 2m 34s
Được tạo vào: 2026-05-12 14:30:00 UTC+7
```

### Truy cập dữ liệu lịch sử

#### Truy vấn cơ sở dữ liệu

Dữ liệu thô được lưu trong SQLite tại `data/dailycheck.db`:

```bash
# Xem tất cả các kết quả gần đây
sqlite3 data/dailycheck.db
> SELECT * FROM check_results ORDER BY timestamp DESC LIMIT 20;

# Đếm theo trạng thái
> SELECT status, COUNT(*) FROM check_results GROUP BY status;

# Kết quả từ ngày cụ thể
> SELECT * FROM check_results WHERE DATE(timestamp) = '2026-05-12';
```

#### Truy cập API

```bash
# Lấy kết quả (định dạng JSON)
curl http://localhost:5000/api/results

# Lấy kết quả cho mô-đun cụ thể
curl http://localhost:5000/api/modules/3/runs
```

### Giữ lại dữ liệu

- **Báo cáo:** Được giữ vô thời hạn (lưu trữ cục bộ)
- **Cơ sở dữ liệu:** Bị giới hạn bởi kích thước `data/dailycheck.db`
- **Nhật ký:** Xoay hàng ngày trong thư mục `logs/`

**Lưu trữ báo cáo cũ:**

```bash
# Tạo kho lưu trữ hàng tháng
tar -czf reports_may_2026.tar.gz data/reports/report_202605*.html

# Xóa các tệp được lưu trữ
rm data/reports/report_202605*.html
```

---

## Xử lý sự cố

### Các vấn đề phổ biến và giải pháp

#### 1. Kiểm tra không chạy

**Vấn đề:** Các kiểm tra được lên lịch không thực hiện vào thời gian được cấu hình

**Chẩn đoán:**
```bash
# Kiểm tra xem hệ thống đang chạy không
ps aux | grep "python main.py"

# Kiểm tra nhật ký
tail -f logs/dailycheck.log

# Xác minh cấu hình
python -c "from src.config_manager import get; print(get('general.check_time'))"
```

**Giải pháp:**
- Xác minh thời gian hệ thống/múi giờ phù hợp với múi giờ cấu hình
- Kiểm tra scheduler đang chạy (không chỉ dashboard)
- Xem lại tệp nhật ký để tìm lỗi
- Đảm bảo không có kiểm tra trước đó vẫn đang chạy (thời gian cứu lại: 300 giây)

#### 2. Lỗi kết nối SSH

**Vấn đề:** "SSH connection refused" hoặc "Permission denied"

**Chẩn đoán:**
```bash
# Kiểm tra kết nối SSH theo cách thủ công
ssh -i ~/.ssh/id_rsa user@target-host "echo test"

# Kiểm tra cấu hình SSH
cat config/config.yaml | grep -A5 "ssh_defaults"
```

**Giải pháp:**
- Xác minh khóa SSH có quyền chính xác: `chmod 600 ~/.ssh/id_rsa`
- Kiểm tra SSH theo cách thủ công để xác nhận kết nối
- Kiểm tra tên người dùng và mật khẩu trong cấu hình khớp với người dùng thực tế
- Xác minh tường lửa cho phép SSH (cổng 22 hoặc cổng được cấu hình)
- Đảm bảo khóa SSH được thêm vào ssh-agent: `ssh-add ~/.ssh/id_rsa`

#### 3. Dashboard không thể truy cập

**Vấn đề:** "Cannot connect to localhost:5000"

**Chẩn đoán:**
```bash
# Kiểm tra xem tiến trình Flask có chạy không
ps aux | grep "flask\|main.py"

# Kiểm tra xem cổng có đang sử dụng không
netstat -tlnp | grep 5000

# Kiểm tra nhật ký cho lỗi Flask
tail -20 logs/dashboard.log
```

**Giải pháp:**
- Đảm bảo dashboard được bật (kiểm tra lệnh khởi động)
- Thử đổi cổng trong cấu hình: `dashboard.port: 8000`
- Giết mọi tiến trình hiện có trên cổng 5000: `lsof -ti:5000 | xargs kill -9`
- Khởi động lại hệ thống: `python main.py`

#### 4. Sử dụng bộ nhớ cao

**Vấn đề:** Hệ thống tiêu thụ quá nhiều bộ nhớ

**Giải pháp:**
- Giảm `max_workers` trong cấu hình (ví dụ: từ 10 xuống 5)
- Tăng timeout đôi chút (có thể giảm lần thử)
- Kiểm tra rò rỉ kết nối SSH: `ss -tp | grep ssh | wc -l`
- Khởi động lại hệ thống định kỳ nếu phát hiện rò rỉ

#### 5. Email không được gửi

**Vấn đề:** Email cảnh báo không được nhận

**Chẩn đoán:**
```bash
# Kiểm tra cấu hình cảnh báo
grep -A10 "^alert:" config/config.yaml

# Kiểm tra nhật ký để tìm lỗi SMTP
grep -i "smtp\|email\|mail" logs/alert_manager.log
```

**Giải pháp:**
- Xác minh thông tin xác thực SMTP trong cấu hình
- Kiểm tra kết nối SMTP: 
  ```bash
  python -c "import smtplib; s = smtplib.SMTP('mail.example.com', 587); s.starttls(); print('OK')"
  ```
- Kiểm tra hạn chế tường lửa miền/cổng email
- Bật mật khẩu dành riêng cho ứng dụng nếu sử dụng Gmail/O365
- Xem lại cấu hình cảnh báo cho người nhận chính xác

#### 6. Lỗi cơ sở dữ liệu bị khóa

**Vấn đề:** Lỗi "database is locked" trong nhật ký

**Giải pháp:**
- Chỉ một instance nên truy cập cơ sở dữ liệu
- Giết các tiến trình python trùng lặp: `pkill -f "python main.py"`
- Chờ 30-60 giây để khóa được xóa
- Nếu tiếp tục, sao lưu và xóa `data/dailycheck.db`:
  ```bash
  cp data/dailycheck.db data/dailycheck.db.backup
  rm data/dailycheck.db
  # Hệ thống sẽ tạo lại trên lần chạy tiếp theo
  ```

#### 7. Cấu hình không được tải

**Vấn đề:** Những thay đổi vào config.yaml không có hiệu lực

**Giải pháp:**
- Phải khởi động lại hệ thống để các thay đổi cấu hình có hiệu lực:
  ```bash
  pkill -f "python main.py"
  python main.py  # Khởi động lại
  ```
- Xác minh cú pháp YAML là chính xác: `python -m yaml config/config.yaml`
- Kiểm tra lỗi cấu hình: `grep -i "error\|warning" logs/*.log`

#### 8. Kiểm tra báo cáo sai tích cực

**Vấn đề:** Kiểm tra báo cáo WARNING/CRITICAL khi không có gì sai

**Giải pháp:**
- Xem xét các giá trị ngưỡng trong cấu hình:
  - `disk_usage_threshold`
  - `tablespace_threshold`
  - `memory_usage_threshold`
- Xác minh các mẫu regex để phân tích đầu ra
- Kiểm tra lệnh thủ công trên các máy chủ đích
- Kiểm tra các vấn đề khoảng trắng hoặc định dạng trong đầu ra
- Tăng `ssh_timeout` nếu các lệnh hết thời gian chờ không chính xác

#### 9. Mô-đun không chạy theo lịch

**Vấn đề:** Dynamic module không thực hiện vào thời gian được lên lịch

**Giải pháp:**
- Xác minh biểu thức cron: Kiểm tra tại `https://crontab.guru/`
- Kiểm tra xem mô-đun có được bật trong dashboard không
- Xem lại nhật ký scheduler để tìm lỗi
- Đảm bảo lần chạy trước đó hoàn thành trước khi bắt đầu lần tiếp theo
- Thử kích hoạt thủ công: Nhấp vào "Run Now" trong dashboard

#### 10. Nhật ký phát triển quá lớn

**Vấn đề:** Tệp nhật ký tiêu thụ quá nhiều dung lượng đĩa

**Giải pháp:**
- Lưu trữ nhật ký cũ:
  ```bash
  tar -czf logs_may_2026.tar.gz logs/dailycheck_2026-05*.log
  rm logs/dailycheck_2026-05*.log
  ```
- Giảm mức độ chi tiết nhật ký trong cấu hình
- Bật xoay nhật ký trong dịch vụ systemd
- Thiết lập làm sạch nhật ký tự động:
  ```bash
  # Trong crontab: Xóa nhật ký cũ hơn 30 ngày
  0 0 * * * find logs -name "*.log" -mtime +30 -delete
  ```

### Chế độ Debug

Bật nhật ký chi tiết:

```bash
# Kiểm tra cờ debug trong cấu hình
grep -i "debug" config/config.yaml

# Bật trong config.yaml:
general:
  debug: true  # Thêm dòng này

# Sau đó khởi động lại hệ thống
python main.py
```

### Tệp nhật ký

**Vị trí nhật ký chính:** `logs/dailycheck.log`

**Tệp nhật ký riêng biệt:**
- `logs/scheduler.log` – Sự kiện scheduler
- `logs/dashboard.log` – Lỗi máy chủ web
- `logs/alert_manager.log` – Thông báo email/webhook
- `logs/checks.log` – Chi tiết thực thi kiểm tra

**Xem nhật ký theo thời gian thực:**
```bash
tail -f logs/dailycheck.log
```

**Tìm kiếm nhật ký để tìm lỗi:**
```bash
grep -i "error\|critical" logs/*.log | head -20
```

### Nhận trợ giúp

Nếu các vấn đề vẫn tồn tại:

1. **Kiểm tra tài liệu:** Xem lại hướng dẫn này và ARCHITECTURE.md
2. **Thu thập nhật ký:** Sao chép 50 dòng cuối cùng của nhật ký
3. **Thu thập thông tin:** Đầu ra của `python main.py --run-now` và cấu hình hệ thống
4. **Kiểm tra trạng thái:** Chạy `curl http://localhost:5000/api/status`

---

## Best Practices

### Best Practices vận hành

#### 1. Sao lưu thường xuyên

```bash
# Sao lưu cấu hình
cp config/config.yaml config/config.yaml.$(date +%Y%m%d)

# Sao lưu cơ sở dữ liệu
cp data/dailycheck.db data/dailycheck.db.$(date +%Y%m%d)

# Sao lưu báo cáo (hàng tháng)
tar -czf "data/reports_$(date +%Y%m).tar.gz" data/reports/
```

#### 2. Kiểm tra thường xuyên

- **Hàng tuần:** Chạy `python main.py --run-now` theo cách thủ công và xác minh kết quả
- **Hàng tháng:** Kiểm tra kết nối SSH cho tất cả các máy chủ được giám sát
- **Hàng quý:** Kiểm tra hệ thống cảnh báo email bằng cách kích hoạt vấn đề nhân tạo

#### 3. Giám sát hệ thống

Giám sát bản thân việc giám sát:

```bash
# Thêm vào cron: Kiểm tra xem SIMO có đang chạy không
0 9 * * * pgrep -f "python main.py" > /dev/null || echo "SIMO down" | mail -s "Alert" ops@example.com
```

#### 4. Tối ưu hóa hiệu năng

- **Thực thi song song:** Điều chỉnh `max_workers` cho cơ sở hạ tầng của bạn
- **Timeouts:** Đặt phù hợp (không quá ngắn, không quá dài)
- **Lên lịch:** Tránh các giờ cao điểm kinh doanh cho kiểm tra
- **Giám sát:** Giám sát sử dụng CPU/bộ nhớ trong quá trình thực hiện kiểm tra

### Best Practices bảo mật

#### 1. Kiểm soát truy cập

```bash
# Giới hạn truy cập vào cấu hình
chmod 600 config/config.yaml
chmod 700 config/

# Giới hạn truy cập cơ sở dữ liệu
chmod 600 data/dailycheck.db
chmod 700 data/
```

#### 2. Bảo mật SSH

```bash
# Sử dụng xác thực dựa trên khóa (không có mật khẩu)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa
ssh-copy-id -i ~/.ssh/id_rsa.pub user@host

# Xác minh quyền khóa
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_rsa
chmod 644 ~/.ssh/id_rsa.pub
```

#### 3. Quản lý mật khẩu

**Nếu sử dụng mật khẩu:**
- Lưu trữ trong tệp `.env`, không phải config.yaml
- Sử dụng biến môi trường trong production
- Xoay các mật khẩu thường xuyên
- Không bao giờ commit mật khẩu vào kiểm soát phiên bản

**Ví dụ với biến môi trường:**
```bash
export ORACLE_SSH_PASSWORD="actual_password"
# Trong config.yaml: password: ${ORACLE_SSH_PASSWORD}
```

#### 4. Bảo mật Dashboard

```yaml
# Trong config.yaml, đặt khóa bí mật mạnh
dashboard:
  secret_key: "$(head -c 32 /dev/urandom | base64)"  # Tạo khóa ngẫu nhiên
```

Đối với production, hãy xem xét:
- Thêm reverse proxy với xác thực (Nginx + Basic Auth)
- Hạn chế dashboard chỉ cho IP nội bộ
- Sử dụng HTTPS với chứng chỉ tự ký hoặc chứng chỉ hợp lệ

#### 5. Bảo mật cảnh báo

```yaml
# Không bao giờ tiếp lộ dữ liệu nhạy cảm trong cảnh báo
alert:
  email:
    # Bao gồm thông tin liên hệ, không phải cấu hình nhạy cảm
    # Ví dụ: "Database on host1 is down - contact DBA"
    # KHÔNG: "Connection string: ..."
```

### Best Practices bảo trì

#### 1. Cập nhật thường xuyên

```bash
# Cập nhật các gói Python theo quý
source venv/bin/activate
pip install --upgrade -r requirements.txt

# Kiểm tra sau cập nhật
python main.py --run-now
```

#### 2. Bảo trì cơ sở dữ liệu

```bash
# Kiểm tra tính toàn vẹn cơ sở dữ liệu hàng tháng
sqlite3 data/dailycheck.db "PRAGMA integrity_check;"

# Tối ưu hóa cơ sở dữ liệu
sqlite3 data/dailycheck.db "VACUUM;"

# Sao lưu trước bảo trì
cp data/dailycheck.db data/dailycheck.db.backup
```

#### 3. Xoay nhật ký

Thiết lập làm sạch nhật ký tự động (tiết kiệm dung lượng đĩa):

```bash
# Thêm vào crontab (giữ 30 ngày nhật ký)
0 1 * * * find /home/simo/dailycheck_simo/logs -name "*.log" -mtime +30 -delete
```

#### 4. Kiểm soát phiên bản

```bash
# Theo dõi các thay đổi cấu hình (không có mật khẩu)
git add config/config.yaml.example
git add *.md requirements.txt
git commit -m "Updated config structure"

# Không bao giờ commit cấu hình thực tế
echo "config/config.yaml" >> .gitignore
echo "data/" >> .gitignore
```

### Best Practices mở rộng quy mô

#### Đối với các triển khai lớn

**Nhiều instance:**
- Chạy dashboard trên một máy chủ
- Chạy scheduler trên máy chủ khác
- Chia sẻ cơ sở dữ liệu duy nhất qua mount mạng

**Hiệu năng:**
- Tăng `max_workers` (với giám sát)
- Sử dụng multiplexing SSH để tái sử dụng kết nối
- Xem xét phần cứng chuyên dụng cho cơ sở hạ tầng lớn

**Tính sẵn sàng cao:**
- Thiết lập chính-phụ cho cơ sở dữ liệu
- Failover tự động sử dụng systemd hoặc các công cụ khác
- Đồng bộ hóa cấu hình thường xuyên

### Giám sát bộ giám sát

**Các chỉ số chính để theo dõi:**

1. **Thời gian thực hiện kiểm tra**
   - Nên nhất quán
   - Cảnh báo nếu vượt quá 5 phút

2. **Lỗi kết nối SSH**
   - Nên bằng không trong môi trường ổn định
   - Theo dõi và điều tra xu hướng

3. **Giao hàng cảnh báo**
   - Xác minh email được nhận theo lịch
   - Theo dõi các lỗi webhook

4. **Sử dụng tài nguyên hệ thống**
   - CPU: Nên tăng đột biến trong kiểm tra, trở về đường cơ sở
   - Bộ nhớ: Không nên phát triển vô giới hạn
   - Đĩa: Giám sát mức độ tăng tệp nhật ký

**Ví dụ về tập lệnh giám sát:**

```bash
#!/bin/bash
# simo-monitor.sh - Kiểm tra sức khỏe SIMO

# Kiểm tra xem nó có chạy không
if ! pgrep -f "python main.py" > /dev/null; then
    echo "ERROR: SIMO not running"
    systemctl restart simo-dailycheck
fi

# Kiểm tra sức khỏe dashboard
if ! curl -s http://localhost:5000/api/status | grep -q "ok"; then
    echo "ERROR: Dashboard unhealthy"
fi

# Kiểm tra kết quả gần đây
LAST_RUN=$(sqlite3 data/dailycheck.db "SELECT MAX(timestamp) FROM check_results;")
if [[ -z "$LAST_RUN" ]]; then
    echo "ERROR: No recent check results"
fi

echo "SIMO health check passed"
```

---

## Phụ lục: Tham chiếu nhanh

### Tham chiếu lệnh nhanh

```bash
# Khởi động hệ thống đầy đủ
python main.py

# Chạy một lần và thoát
python main.py --run-now

# Kiểm tra xem nó có chạy không
ps aux | grep "python main.py"

# Xem nhật ký
tail -f logs/dailycheck.log

# Truy vấn cơ sở dữ liệu
sqlite3 data/dailycheck.db

# Dừng hệ thống một cách khéo léo
kill $(pgrep -f "python main.py")

# Sao lưu mọi thứ
tar -czf simo_backup_$(date +%Y%m%d).tar.gz config/ data/ logs/
```

### Tham chiếu cấu hình nhanh

**Tham số cấu hình chính:**

| Tham số | Mặc định | Tác động |
|-----------|---------|--------|
| `check_time` | "08:00" | Khi nào để chạy kiểm tra hàng ngày |
| `max_workers` | 10 | Các luồng kiểm tra song song |
| `ssh_timeout` | 30s | Timeout kết nối SSH |
| `command_timeout` | 60s | Timeout thực thi lệnh |
| `dashboard.port` | 5000 | Cổng giao diện web |

### Tham chiếu API nhanh

| Điểm cuối | Phương pháp | Mục đích |
|----------|--------|---------|
| `/api/status` | GET | Kiểm tra sức khỏe hệ thống |
| `/api/results` | GET | Kết quả kiểm tra mới nhất |
| `/api/modules` | GET | Liệt kê dynamic modules |
| `/api/modules/<id>/trigger` | POST | Chạy mô-đun ngay lập tức |
| `/api/modules/<id>/runs` | GET | Lịch sử chạy mô-đun |

### Các lệnh hệ thống hữu ích

```bash
# Kiểm tra thời gian/múi giờ hệ thống
date
timedatectl

# Kiểm tra kết nối mạng
ping -c 1 oracle-host
ssh -v user@oracle-host

# Giám sát tài nguyên hệ thống
top
free -h
df -h

# Kiểm tra cổng Flask
netstat -tlnp | grep 5000
lsof -i :5000
```

---

## Lịch sử tài liệu

| Phiên bản | Ngày | Thay đổi |
|---------|------|---------|
| 1.0 | 2026-05-12 | Tạo hướng dẫn sử dụng ban đầu |

---

**Để xem tài liệu kỹ thuật, hãy xem:** [ARCHITECTURE.md](ARCHITECTURE.md)  
**Để xem ghi chú agent, hãy xem:** [AGENTS.md](AGENTS.md)  
**Để xem thông tin phát triển, hãy xem:** [README.md](README.md)

---

*Cập nhật lần cuối: 12 tháng 5 năm 2026*

