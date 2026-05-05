# Hướng dẫn sử dụng SIMO Daily Check System

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Yêu cầu hệ thống](#2-yêu-cầu-hệ-thống)
3. [Khởi động chương trình](#3-khởi-động-chương-trình)
4. [Cấu hình ban đầu](#4-cấu-hình-ban-đầu)
   - 4.1 [Tạo hệ thống (System)](#41-tạo-hệ-thống-system)
   - 4.2 [Thêm host](#42-thêm-host)
   - 4.3 [Tạo plan](#43-tạo-plan)
   - 4.4 [Thêm bước kiểm tra (Step)](#44-thêm-bước-kiểm-tra-step)
5. [Tham số từng loại bước kiểm tra](#5-tham-số-từng-loại-bước-kiểm-tra)
   - 5.1 [Oracle – 11 bước](#51-oracle--11-bước)
   - 5.2 [MongoDB – 3 bước](#52-mongodb--3-bước)
   - 5.3 [Hadoop – 3 bước](#53-hadoop--3-bước)
   - 5.4 [Application – 4 bước](#54-application--4-bước)
6. [Chạy kiểm tra](#6-chạy-kiểm-tra)
7. [Xem kết quả và báo cáo](#7-xem-kết-quả-và-báo-cáo)
8. [Cấu hình thông báo email](#8-cấu-hình-thông-báo-email)
9. [Advanced JSON](#9-advanced-json)
10. [Mẫu cấu hình SIMO theo tài liệu](#10-mẫu-cấu-hình-simo-theo-tài-liệu)
11. [Hướng dẫn nhập form cụ thể theo tài liệu SIMO](#11-hướng-dẫn-nhập-form-cụ-thể-theo-tài-liệu-simo)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Tổng quan

**SIMO Daily Check System** là công cụ kiểm tra tự động đầu ngày làm việc cho các hệ thống:

| Loại | Mô tả |
|------|-------|
| **Oracle** | Kiểm tra RAC/CRS/OCR/Voting Disk/Tablespace/ASM/Alert Log |
| **MongoDB** | Kiểm tra service, phân vùng đĩa |
| **Hadoop** | Kiểm tra HDFS DataNode, Safe Mode, phân vùng đĩa |
| **Application** | Kiểm tra service systemctl, Docker containers |

**Đặc điểm chính:**
- Toàn bộ tham số kiểm tra (lệnh, ngưỡng, SQL, keyword, expected output) được nhập từ Dashboard — không có giá trị cứng trong code.
- Hỗ trợ nhiều hệ thống chạy song song.
- Lưu lịch sử kết quả, gửi báo cáo HTML qua email.
- Chạy tự động lúc 08:00 mỗi ngày hoặc kích hoạt thủ công.

---

## 2. Yêu cầu hệ thống

| Thành phần | Phiên bản tối thiểu |
|------------|---------------------|
| Docker | 20.10+ |
| Docker Compose | 2.0+ |
| SSH key | Đã tạo và đặt vào `~/.ssh/` |

---

## 3. Khởi động chương trình

### Bước 1 – Tạo file cấu hình

```bash
cat > config/config.yaml << 'EOF'
general:
  check_time: "08:00"        # Giờ chạy tự động (HH:MM)
  prep_time: "07:55"         # Giờ khởi động trước để warm SSH
  ssh_timeout: 30
  ssh_connect_timeout: 15
  max_workers: 10
  data_dir: "data"

dashboard:
  host: "0.0.0.0"
  port: 5000
  debug: false
  secret_key: "đổi-thành-chuỗi-bí-mật-riêng"

notifications:
  email:
    enabled: false
    smtp_host: "smtp.company.com"
    smtp_port: 587
    sender: "dailycheck@company.com"
    recipients:
      - "admin@company.com"
    username: ""
    password: ""
  webhook:
    enabled: false
    url: ""
EOF
```

### Bước 2 – Build và chạy Docker

```bash
docker compose up --build -d
```

> **Lưu ý:** Nếu port 5000 bị macOS AirPlay Receiver chiếm, sửa `docker-compose.yml`:
> ```yaml
> ports:
>   - "5001:5000"
> ```

### Bước 3 – Truy cập Dashboard

Mở trình duyệt: **http://localhost:5000** (hoặc `:5001` nếu đổi port)

### Kiểm tra container

```bash
docker logs simo_dailycheck --tail 30
docker ps
```

### Dừng chương trình

```bash
docker compose down
```

---

## 4. Cấu hình ban đầu

> **Quy trình:** Tạo System → Thêm Host → Tạo Plan → Thêm Step

### 4.1 Tạo hệ thống (System)

1. Vào menu **Systems** trên thanh điều hướng.
2. Nhấn **+ Thêm hệ thống**.
3. Nhập thông tin:

| Trường | Bắt buộc | Ví dụ |
|--------|----------|-------|
| Tên hệ thống | ✅ | `Oracle SIMO` |
| Loại hệ thống | ✅ | `oracle` / `mongodb` / `hadoop` / `application` |
| Mô tả | | `Database chính SIMO` |
| Enabled | | Bật/tắt hệ thống |

4. Nhấn **Lưu**.

### 4.2 Thêm host

1. Từ trang **Systems**, nhấn **Hosts** bên cạnh hệ thống vừa tạo.
2. Nhấn **+ Thêm host**.
3. Nhập thông tin:

| Trường | Bắt buộc | Ví dụ |
|--------|----------|-------|
| Tên host | ✅ | `db1` |
| IP | ✅ | `10.1.36.11` |
| Username | ✅ | `oracle` |
| SSH port | | `22` |
| SSH key path | | `~/.ssh/id_rsa` |
| Password | | (nếu không dùng key) |
| Role | | `primary` / `secondary` |
| **Extra JSON** | | Xem bảng bên dưới |

#### Extra JSON theo loại hệ thống

**Oracle:**
```json
{
  "oracle_sid": "simo1",
  "asm_sid": "+ASM1"
}
```

**MongoDB:** *(thường không cần extra)*
```json
{}
```

**Hadoop:**
```json
{
  "hdfs_user": "hdfs"
}
```

**Application:** *(thường không cần extra)*
```json
{}
```

> **Lưu ý Oracle RAC:** Mỗi node (DB1, DB2) có `oracle_sid` khác nhau, ví dụ: `simo1` trên DB1 và `simo2` trên DB2.

### 4.3 Tạo plan

1. Vào menu **Plans**.
2. Nhấn **+ Tạo plan**.
3. Nhập thông tin:

| Trường | Mô tả |
|--------|-------|
| Tên plan | Tên nhận diện, ví dụ: `SIMO Daily Check` |
| Mô tả | Ghi chú thêm |
| Default | Tích nếu muốn scheduler tự động dùng plan này |
| Enabled | Bật/tắt plan |

4. Nhấn **Lưu**.

### 4.4 Thêm bước kiểm tra (Step)

1. Từ trang **Plans**, nhấn **Editor** bên cạnh plan.
2. Nhấn **+ Thêm step**.
3. Cấu hình:
   - **Hệ thống**: Chọn system đã tạo.
   - **Step**: Chọn loại bước kiểm tra từ danh sách.
   - **Thứ tự**: Số thứ tự chạy (1, 2, 3…).
   - **Host scope**: Chọn host nào sẽ chạy bước này.
   - **Tham số**: Nhập theo từng loại bước (xem mục 5).
   - **Advanced JSON**: Nhập JSON thêm cho tham số phức tạp.
4. Nhấn **Lưu**.

#### Host scope

| Chế độ | Ý nghĩa |
|--------|---------|
| `Tất cả host` | Chạy trên mọi host của hệ thống |
| `Chỉ host được chọn` | Tick chọn các host cụ thể |
| `Host đầu tiên` | Chỉ chạy trên host đầu tiên (cho bước chỉ cần 1 máy) |

---

## 5. Tham số từng loại bước kiểm tra

### 5.1 Oracle – 11 bước

#### Bước 1 – Thông số hệ thống (`system_info`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Các lệnh thực thi | ✅ | `date`, `hostname` (mỗi dòng một lệnh) |

**Host scope:** Tất cả host (cả 2 máy DB1/DB2)

---

#### Bước 2 – Phân vùng đĩa (`disk_space`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Lệnh kiểm tra | ✅ | `df -k` |
| Ngưỡng %used | ✅ | `90` |
| Mount loại trừ | | `/cdrom` |

**Host scope:** Tất cả host

---

#### Bước 3 – CRS Status (`crs_status`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Lệnh CRS | ✅ | `crs_stat -t` |
| Fallback command | | `crsctl stat res -t` |
| Resource được phép OFFLINE | | `ora.gsd`, `ora.oc4j`, `ora....bkup.db` (mỗi dòng một tên) |

**Host scope:** Host đầu tiên (thực hiện trên 1 máy)

---

#### Bước 4 – OCR Check (`ocr`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Lệnh kiểm tra OCR | ✅ | `ocrcheck` |
| Chuỗi yêu cầu xuất hiện | ✅ | `Device/File integrity check succeeded`, `Cluster registry integrity check succeeded` |

**Host scope:** Host đầu tiên

---

#### Bước 5 – Voting Disk (`voting_disk`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Lệnh kiểm tra | ✅ | `crsctl query css votedisk` |
| Số voting disk ONLINE yêu cầu | ✅ | `3` |

**Host scope:** Host đầu tiên

---

#### Bước 6 – Archive Switch (`archive_switch`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Tên field ORACLE_SID trong host.extra | ✅ | `oracle_sid` |
| SQL | ✅ | `alter system archive log current;` |
| Chuỗi yêu cầu | ✅ | `System altered` |

**Host scope:** Host đầu tiên

---

#### Bước 7 – Free Tablespace (`free_tablespace`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Tên field ORACLE_SID | ✅ | `oracle_sid` |
| SQL kiểm tra tablespace | ✅ | *(SQL đầy đủ, xem mục 10)* |
| System tablespace names | ✅ | `SYSTEM`, `SYSAUX` (mỗi dòng một tên) |
| System min %Free | ✅ | `3` |
| Prefix UNDO tablespace | ✅ | `UNDOTBS` |
| UNDO min Total MB | ✅ | `400` |
| Default min %Free | ✅ | `15` |

**Host scope:** Host đầu tiên

---

#### Bước 8 – Unusable Indexes (`unusable_indexes`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Tên field ORACLE_SID | ✅ | `oracle_sid` |
| SQL kiểm tra | ✅ | *(SQL đầy đủ, xem mục 10)* |
| Chuỗi báo rỗng | ✅ | `no rows selected` |

**Host scope:** Host đầu tiên

---

#### Bước 9 – Invalid Objects (`invalid_objects`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Tên field ORACLE_SID | ✅ | `oracle_sid` |
| SQL kiểm tra | ✅ | *(SQL đầy đủ, xem mục 10)* |
| Chuỗi báo rỗng | ✅ | `no rows selected` |

**Host scope:** Host đầu tiên

---

#### Bước 10 – ASM Disk Group (`asm_diskgroup`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Tên field ASM SID trong host.extra | ✅ | `asm_sid` |
| SQL kiểm tra ASM | ✅ | `select name,TOTAL_MB,FREE_MB, trunc(((TOTAL_MB-FREE_MB)/TOTAL_MB)*100,2) "%used" from v$asm_diskgroup;` |
| Ngưỡng %used | ✅ | `90` |

**Host scope:** Host đầu tiên

---

#### Bước 11 – Alert Log (`alert_log`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Tên field ORACLE_SID | ✅ | `oracle_sid` |
| SQL lấy đường dẫn alert log | ✅ | `show parameter background_dump_dest;` |
| Tên field trong output | ✅ | `background_dump_dest` |
| Template lệnh quét log | ✅ | `tail -1000 {alert_dir}/alert_{instance}.log \| egrep '{keywords}'` |
| Keywords lỗi | ✅ | `ORA-`, `TNS-`, `Errors` (mỗi dòng một keyword) |

**Host scope:** Tất cả host (cả 2 máy DB1/DB2)

---

### 5.2 MongoDB – 3 bước

#### Bước 1 – Thông số hệ thống (`system_info`)
```
Lệnh: date, hostname
Host scope: Tất cả host
```

#### Bước 2 – Phân vùng đĩa (`disk_space`)
```
Lệnh: df -kh
Ngưỡng: 90
Mount loại trừ: /cdrom
Host scope: Tất cả host
```

#### Bước 3 – Trạng thái MongoDB (`service_status`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Lệnh systemctl | ✅ | `systemctl status mongod` |
| Chuỗi yêu cầu | ✅ | `active (running)` |

**Host scope:** Host đầu tiên

---

### 5.3 Hadoop – 3 bước

#### Bước 1 – Thông số hệ thống (`system_info`)
```
Lệnh: date, hostname
Host scope: Tất cả host
```

#### Bước 2 – Phân vùng đĩa (`disk_space`)
```
Lệnh: df -kh
Ngưỡng: 90
Host scope: Tất cả host
```

#### Bước 3 – HDFS Report (`hdfs_report`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Lệnh HDFS | ✅ | `hdfs dfsadmin -report` |
| Mẫu Safe Mode báo lỗi | ✅ | `safe mode is ON` |
| Số live DataNode tối thiểu | ✅ | `2` |
| Ngưỡng DFS Used % | ✅ | `85` |

**Host scope:** Host đầu tiên (NameNode)

---

### 5.4 Application – 4 bước

#### Bước 1 – Thông số hệ thống (`system_info`)
```
Lệnh: date, hostname
Host scope: Tất cả host
```

#### Bước 2 – Phân vùng đĩa (`disk_space`)
```
Lệnh: df -kh
Ngưỡng: 90
Host scope: Tất cả host
```

#### Bước 3 – Service Status (`service_status`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Lệnh kiểm tra service | ✅ | `systemctl status wso2am-3.2.0.service` |
| Chuỗi yêu cầu | ✅ | `active (running)` |

---

#### Bước 3b – Docker Containers (`docker_containers`)

| Tham số | Bắt buộc | Ví dụ |
|---------|----------|-------|
| Lệnh docker ps | ✅ | `docker ps --format '{{.Names}}\t{{.Status}}'` |
| Chuỗi container running | ✅ | `Up` |
| Lệnh docker service ls | | `docker service ls --format '{{.Name}}\t{{.Replicas}}'` |

---

## 6. Chạy kiểm tra

### Chạy thủ công

**Cách 1 – Từ trang Plans:**
1. Vào **Plans**.
2. Nhấn **▶ Run** bên cạnh plan muốn chạy.
3. Xác nhận → Hệ thống chạy nền.

**Cách 2 – Từ Plan Editor:**
1. Vào **Plans → Editor**.
2. Nhấn **▶ Chạy plan này** góc trên phải.

**Cách 3 – API:**
```bash
curl -X POST http://localhost:5001/api/run-plan \
  -H "Content-Type: application/json" \
  -d '{"plan_id": 1}'
```

### Chạy tự động

Scheduler tự động chạy mỗi ngày lúc `check_time` trong `config/config.yaml` (mặc định **08:00**).

Để thay giờ, sửa `config/config.yaml`:
```yaml
general:
  check_time: "08:00"
```
Restart container để áp dụng:
```bash
docker compose restart
```

---

## 7. Xem kết quả và báo cáo

### Dashboard chính (`/`)

- Hiển thị lịch sử 30 lần chạy gần nhất.
- Kết quả lần chạy mới nhất theo từng hệ thống.
- Nhãn trạng thái: ✅ OK / ⚠️ WARNING / 🔴 CRITICAL / ❌ ERROR.

### Chi tiết lần chạy

Nhấn vào **Run ID** để xem kết quả chi tiết theo từng host và bước kiểm tra.

### Báo cáo HTML

```
/report/<run_id>
```

### API kết quả

```bash
# Danh sách 30 lần chạy gần nhất
curl http://localhost:5001/api/runs

# Chi tiết 1 lần chạy
curl http://localhost:5001/api/run/<run_id>

# Trạng thái hiện tại
curl http://localhost:5001/api/status
```

---

## 8. Cấu hình thông báo email

Sửa `config/config.yaml`:

```yaml
notifications:
  email:
    enabled: true
    smtp_host: "smtp.company.com"
    smtp_connect_host: "10.1.1.100"   # IP thực nếu DNS không resolve được
    smtp_port: 587
    smtp_use_tls: true
    sender: "dailycheck@company.com"
    recipients:
      - "admin@company.com"
      - "team@company.com"
    username: "dailycheck@company.com"
    password: "mat-khau"
    smtp_resolve_retries: 3
    smtp_resolve_retry_delay_sec: 2
```

Restart container sau khi sửa:
```bash
docker compose restart
```

---

## 9. Advanced JSON

Dành cho các tham số phức tạp không vừa form, ví dụ SQL nhiều dòng.

**Ví dụ nhập SQL Tablespace vào Advanced JSON:**

```json
{
  "sql": "COLUMN pct_free FORMAT 999.99 HEADING \"% Free\"\nCOLUMN name FORMAT A16\nSELECT fs.tablespace_name name, df.totalspace mbytes, (df.totalspace - fs.freespace) used, fs.freespace free, 100 * (fs.freespace / df.totalspace) pct_free FROM (SELECT tablespace_name, ROUND(SUM(bytes) / 1048576) TotalSpace FROM dba_data_files GROUP BY tablespace_name) df, (SELECT tablespace_name, ROUND(SUM(bytes) / 1048576) FreeSpace FROM dba_free_space GROUP BY tablespace_name) fs WHERE df.tablespace_name = fs.tablespace_name(+);"
}
```

> **Quy tắc merge:** Advanced JSON được merge với form tham số; nếu cùng tên field thì **form có ưu tiên** ghi đè JSON.

---

## 10. Mẫu cấu hình SIMO theo tài liệu

Bảng tra cứu nhanh để nhập step cho hệ thống Oracle SIMO (`Simo_master`, RAC 2 node DB1/DB2).

### SQL Tablespace (Bước 7)

```sql
COLUMN pct_free FORMAT 999.99 HEADING "% Free"
COLUMN name FORMAT A16 HEADING "Tablespace Name"
COLUMN mbytes FORMAT 99,999,999 HEADING "Total MBytes"
COLUMN used FORMAT 99,999,999 HEADING "Used Mbytes"
COLUMN free FORMAT 99,999,999 HEADING "Free Mbytes"
BREAK ON REPORT
COMPUTE SUM OF mbytes ON REPORT
COMPUTE SUM OF free ON REPORT
COMPUTE SUM OF used ON REPORT
SELECT
  fs.tablespace_name name,
  df.totalspace mbytes,
  (df.totalspace - fs.freespace) used,
  fs.freespace free,
  100 * (fs.freespace / df.totalspace) pct_free
FROM
  (SELECT tablespace_name, ROUND(SUM(bytes) / 1048576) TotalSpace
   FROM dba_data_files GROUP BY tablespace_name) df,
  (SELECT tablespace_name, ROUND(SUM(bytes) / 1048576) FreeSpace
   FROM dba_free_space GROUP BY tablespace_name) fs
WHERE df.tablespace_name = fs.tablespace_name(+);
```

**Tham số ngưỡng theo tài liệu:**
- `system_tablespaces`: `SYSTEM`, `SYSAUX`, `EXAMPLE`
- `system_min_free_pct`: `3`
- `undo_prefix`: `UNDOTBS`
- `undo_min_total_mb`: `400`
- `default_min_free_pct`: `15`

### SQL Unusable Indexes (Bước 8)

```sql
select ' alter index '||owner || '.' || index_name || ' rebuild ; '
  from dba_indexes where STATUS = 'UNUSABLE'
   union all
      select ' alter index '||INDEX_OWNER || '.' || index_name ||
             ' rebuild  partition ' ||partition_name || '; '
         from dba_ind_partitions where STATUS = 'UNUSABLE'
          union all
             select ' alter index '||INDEX_OWNER || '.' || index_name ||
                    ' rebuild  subpartition ' ||subpartition_name || '; '
                from dba_ind_subpartitions where STATUS = 'UNUSABLE';
```

### SQL Invalid Objects (Bước 9)

```sql
select
  'ALTER ' || OBJECT_TYPE || ' ' ||
  OWNER || '.' || OBJECT_NAME || ' COMPILE;'
  from dba_objects
  where status = 'INVALID'
  and object_type in ('PACKAGE','FUNCTION','PROCEDURE','VIEW','SYNONYM')
  and owner in (select username from dba_users where account_status ='OPEN');
```

### SQL ASM Disk Group (Bước 10)

```sql
select name, TOTAL_MB, FREE_MB,
       trunc(((TOTAL_MB-FREE_MB)/TOTAL_MB)*100,2) "%used"
from v$asm_diskgroup;
```

### Template Alert Log (Bước 11)

```
tail -1000 {alert_dir}/alert_{instance}.log | egrep '{keywords}'
```

**Keywords:** `ORA-`, `TNS-`, `Errors`

---

## 11. Hướng dẫn nhập form cụ thể theo tài liệu SIMO

Phần này hướng dẫn nhập dữ liệu trên Dashboard theo đúng nội dung file `1. SIMO_Daily_check_v1.0.docx`.

### 11.1 Quy ước nhập liệu trên form

| Loại field trên form | Cách nhập |
|----------------------|-----------|
| Text | Nhập đúng một dòng lệnh hoặc chuỗi |
| Number | Chỉ nhập số, ví dụ `90`, `3`, `400` |
| List | Mỗi giá trị nhập trên một dòng riêng |
| Textarea | Dán nguyên SQL hoặc lệnh nhiều dòng |
| Advanced JSON | Chỉ dùng khi cần truyền JSON nâng cao; bình thường để `{}` hoặc bỏ trống |

### 11.2 Nhập System theo tài liệu

Vào **Systems → + Thêm hệ thống**, tạo các system sau:

| Tên hệ thống nên nhập | Loại hệ thống | Mô tả |
|-----------------------|---------------|-------|
| `Oracle - Simo_master` | `oracle` | Oracle 19.3, RAC, DB name `Simo_master` |
| `MongoDB - simo` | `mongodb` | MongoDB Cluster, DB name `simo` |
| `Hadoop - SIMO` | `hadoop` | Hadoop Cluster |
| `Application - WSO2 EI Cluster` | `application` | Ứng dụng WSO2 EI |
| `Application - WSO2 APIM Cluster` | `application` | Ứng dụng WSO2 APIM |
| `Application - HAProxy` | `application` | HAProxy Cluster |
| `Application - Kafka` | `application` | Kafka Cluster |
| `Application - App Servers` | `application` | Application Servers |

### 11.3 Nhập Host theo tài liệu

Vào **Systems → Hosts** của từng system và nhập host.

#### Oracle - Simo_master

Tài liệu ghi Host IP: `10.1.36.11` và `10.1.36.12`.

| Field | Host 1 | Host 2 |
|-------|--------|--------|
| Tên host | `oracle-db1` | `oracle-db2` |
| IP | `10.1.36.11` | `10.1.36.12` |
| Username | `oracle` hoặc user vận hành thực tế | `oracle` hoặc user vận hành thực tế |
| SSH port | `22` | `22` |
| SSH key path | `~/.ssh/id_rsa` hoặc key thực tế | `~/.ssh/id_rsa` hoặc key thực tế |
| Role | `db1` | `db2` |

**Extra JSON cho DB1:**

```json
{
  "oracle_sid": "simo1",
  "asm_sid": "+ASM1"
}
```

**Extra JSON cho DB2:**

```json
{
  "oracle_sid": "simo2",
  "asm_sid": "+ASM2"
}
```

> Nếu SID thực tế khác `simo1/simo2`, nhập đúng instance name thực tế theo môi trường.

#### MongoDB - simo

Tài liệu ghi Host IP: `10.1.36.41` và `10.1.36.44`.

| Field | Host 1 | Host 2 |
|-------|--------|--------|
| Tên host | `mongo-1` | `mongo-2` |
| IP | `10.1.36.41` | `10.1.36.44` |
| Username | user SSH thực tế, ví dụ `root` | user SSH thực tế, ví dụ `root` |
| SSH port | `22` | `22` |
| Extra JSON | `{}` | `{}` |

#### Hadoop - SIMO

Tài liệu ghi Host IP: `10.1.36.31`, `10.1.36.34`, `10.1.36.37`.

| Field | Host 1 | Host 2 | Host 3 |
|-------|--------|--------|--------|
| Tên host | `hadoop-1` | `hadoop-2` | `hadoop-3` |
| IP | `10.1.36.31` | `10.1.36.34` | `10.1.36.37` |
| Username | user SSH thực tế, ví dụ `hdfs` | user SSH thực tế | user SSH thực tế |
| SSH port | `22` | `22` | `22` |

**Extra JSON khuyến nghị:**

```json
{
  "hdfs_user": "hdfs"
}
```

#### Application

Tài liệu ghi danh sách IP:

| System | IP cần nhập |
|--------|-------------|
| WSO2 EI Cluster | `10.1.46.31`, `10.1.46.32` |
| WSO2 APIM Cluster | `10.1.16.11`, `10.1.16.12` |
| HAProxy | `10.1.16.17`, `10.1.16.18` |
| Kafka | `10.1.46.23`, `10.1.46.24` |
| Application Servers | `10.1.46.41`, `10.1.46.42`, `10.1.46.43` |

Với mỗi host:

| Field | Cách nhập |
|-------|-----------|
| Tên host | Đặt tên dễ hiểu, ví dụ `wso2-ei-1`, `haproxy-1`, `kafka-1` |
| IP | IP theo bảng trên |
| Username | Theo tài liệu: nhiều bước dùng `root`; nhập user thực tế có quyền chạy `systemctl`/`docker` |
| SSH port | `22` |
| Extra JSON | `{}` |

### 11.4 Tạo Plan

Vào **Plans → + Tạo plan**:

| Field | Giá trị nên nhập |
|-------|------------------|
| Tên plan | `SIMO Daily Check - Theo tài liệu v1.0` |
| Mô tả | `Plan kiểm tra đầu ngày theo tài liệu SIMO_Daily_check_v1.0.docx` |
| Default | Bật nếu muốn scheduler chạy plan này lúc 08:00 |
| Enabled | Bật |

### 11.5 Nhập Step Oracle - Simo_master

Vào **Plans → Editor → + Thêm step** và nhập lần lượt 11 step sau.

#### Oracle Step 1 - Thông số hệ thống

| Field | Giá trị nhập |
|-------|--------------|
| Hệ thống | `Oracle - Simo_master` |
| Step | `Bước 1 - Thông số hệ thống (system_info)` |
| Thứ tự | `1` |
| Host scope | `Tất cả host` |
| Các lệnh thực thi | Nhập mỗi dòng một lệnh |

```text
date
hostname
```

#### Oracle Step 2 - Phân vùng đĩa

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 2 - Phân vùng đĩa (disk_space)` |
| Thứ tự | `2` |
| Host scope | `Tất cả host` |
| Lệnh kiểm tra | `df -k` |
| Ngưỡng %used | `90` |
| Mount loại trừ | `/cdroom` |

> Tài liệu ghi `/cdroom`; nếu môi trường thực tế dùng `/cdrom` thì nhập thêm cả hai dòng.

```text
/cdroom
/cdrom
```

#### Oracle Step 3 - CRS Status

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 3 - CRS Status (crs_status)` |
| Thứ tự | `3` |
| Host scope | `Host đầu tiên` |
| Lệnh CRS | `crs_stat -t` |
| Fallback command | `crsctl stat res -t` |
| Resource được phép OFFLINE | Nhập mỗi resource một dòng |

```text
ora.gsd
ora.oc4j
ora....bkup.db
```

Ý nghĩa kiểm tra theo tài liệu: tất cả dịch vụ Oracle Clusterware phải `ONLINE/ONLINE`, ngoại trừ GSD, OC4J và Database BKUP được phép `OFFLINE/OFFLINE`.

#### Oracle Step 4 - OCR Check

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 4 - OCR Check (ocr)` |
| Thứ tự | `4` |
| Host scope | `Host đầu tiên` |
| Lệnh kiểm tra OCR | `ocrcheck` |
| Chuỗi yêu cầu xuất hiện | Nhập mỗi chuỗi một dòng |

```text
Device/File integrity check succeeded
Cluster registry integrity check succeeded
```

#### Oracle Step 5 - Voting Disk

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 5 - Voting Disk (voting_disk)` |
| Thứ tự | `5` |
| Host scope | `Host đầu tiên` |
| Lệnh kiểm tra | `crsctl query css votedisk` |
| Số voting disk ONLINE yêu cầu | `3` |

Theo tài liệu, output mong đợi có `Located 3 voting disk(s).` và cả 3 dòng `STATE` là `ONLINE`.

#### Oracle Step 6 - Archive Switch

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 6 - Archive Switch (archive_switch)` |
| Thứ tự | `6` |
| Host scope | `Host đầu tiên` |
| Tên field lấy ORACLE_SID từ host.extra | `oracle_sid` |
| Lệnh SQL | `alter system archive log current;` |
| Chuỗi yêu cầu xuất hiện | `System altered` |

#### Oracle Step 7 - Free Tablespace

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 7 - Free Tablespace (free_tablespace)` |
| Thứ tự | `7` |
| Host scope | `Host đầu tiên` |
| Tên field ORACLE_SID | `oracle_sid` |
| SQL kiểm tra tablespace | Dán SQL bên dưới |
| System tablespace names | `SYSTEM`, `SYSAUX`, `EXAMPLE` |
| System min %Free | `3` |
| Prefix UNDO tablespace | `UNDOTBS` |
| UNDO min Total MB | `400` |
| Default min %Free | `15` |

**System tablespace names nhập dạng list:**

```text
SYSTEM
SYSAUX
EXAMPLE
```

**SQL kiểm tra tablespace:**

```sql
COLUMN pct_free FORMAT 999.99 HEADING "% Free"
COLUMN name FORMAT A16 HEADING "Tablespace Name"
COLUMN mbytes FORMAT 99,999,999 HEADING "Total MBytes"
COLUMN used FORMAT 99,999,999 HEADING "Used Mbytes"
COLUMN free FORMAT 99,999,999 HEADING "Free Mbytes"
BREAK ON REPORT
COMPUTE SUM OF mbytes ON REPORT
COMPUTE SUM OF free ON REPORT
COMPUTE SUM OF used ON REPORT
SELECT
fs.tablespace_name name,
df.totalspace mbytes,
(df.totalspace - fs.freespace) used,fs.freespace free,
100 * (fs.freespace / df.totalspace) pct_free
FROM
(SELECT
tablespace_name,
ROUND(SUM(bytes) / 1048576) TotalSpace
FROM
dba_data_files
GROUP BY
tablespace_name
) df,
(SELECT
tablespace_name,
ROUND(SUM(bytes) / 1048576) FreeSpace
FROM
dba_free_space
GROUP BY
tablespace_name
) fs
WHERE
df.tablespace_name = fs.tablespace_name(+);
```

#### Oracle Step 8 - Unusable Indexes

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 8 - Unusable Indexes (unusable_indexes)` |
| Thứ tự | `8` |
| Host scope | `Host đầu tiên` |
| Tên field ORACLE_SID | `oracle_sid` |
| SQL kiểm tra | Dán SQL bên dưới |
| Chuỗi báo rỗng | `no rows selected` |

```sql
select  ' alter index '||owner || '.' || index_name ||  ' rebuild ; '
  from dba_indexes where STATUS  = 'UNUSABLE'
   union all
      select  ' alter index '||INDEX_OWNER || '.' || index_name ||  ' rebuild  partition ' ||partition_name || '; '
         from dba_ind_partitions where STATUS  = 'UNUSABLE'
          union all
             select  ' alter index '||INDEX_OWNER || '.' || index_name ||  ' rebuild  subpartition ' ||subpartition_name || '; '
                from dba_ind_subpartitions where STATUS  = 'UNUSABLE';
```

#### Oracle Step 9 - Invalid Objects

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 9 - Invalid Objects (invalid_objects)` |
| Thứ tự | `9` |
| Host scope | `Host đầu tiên` |
| Tên field ORACLE_SID | `oracle_sid` |
| SQL kiểm tra | Dán SQL bên dưới |
| Chuỗi báo rỗng | `no rows selected` |

```sql
select
   'ALTER ' || OBJECT_TYPE || ' ' ||
      OWNER || '.' || OBJECT_NAME || ' COMPILE;'
      from
         dba_objects
         where
            status = 'INVALID'
            and
               object_type in ('PACKAGE','FUNCTION','PROCEDURE','VIEW','SYNONYM')
and owner in (select username from dba_users where account_status ='OPEN');
```

#### Oracle Step 10 - ASM Disk Group

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 10 - ASM Disk Group (asm_diskgroup)` |
| Thứ tự | `10` |
| Host scope | `Host đầu tiên` |
| Tên field ASM SID từ host.extra | `asm_sid` |
| SQL kiểm tra ASM | SQL bên dưới |
| Ngưỡng %used | `90` |

```sql
select name,TOTAL_MB,FREE_MB, trunc(((TOTAL_MB-FREE_MB)/TOTAL_MB)*100,2) "%used" from v$asm_diskgroup;
```

#### Oracle Step 11 - Alert Log

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 11 - Alert Log (alert_log)` |
| Thứ tự | `11` |
| Host scope | `Tất cả host` |
| Tên field ORACLE_SID | `oracle_sid` |
| SQL lấy đường dẫn alert log | `show parameter dump;` |
| Tên field trong output | `background_dump_dest` |
| Template lệnh quét log | Dòng template bên dưới |
| Keywords lỗi | `ORA-`, `TNS-`, `Errors` |

**Template lệnh quét log:**

```text
tail -1000 {alert_dir}/alert_{instance}.log | egrep '{keywords}'
```

**Keywords lỗi nhập dạng list:**

```text
ORA-
TNS-
Errors
```

### 11.6 Nhập Step MongoDB - simo

#### MongoDB Step 1 - Thông số hệ thống

| Field | Giá trị nhập |
|-------|--------------|
| Hệ thống | `MongoDB - simo` |
| Step | `Bước 1 - Thông số hệ thống (system_info)` |
| Thứ tự | `12` hoặc `1` nếu plan riêng |
| Host scope | `Tất cả host` |
| Các lệnh | `date`, `hostname` |

```text
date
hostname
```

#### MongoDB Step 2 - Phân vùng đĩa

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 2 - Phân vùng đĩa (disk_space)` |
| Thứ tự | `13` hoặc `2` nếu plan riêng |
| Host scope | `Tất cả host` |
| Lệnh kiểm tra | `df -kh` |
| Ngưỡng %used | `90` |
| Mount loại trừ | `/cdroom` hoặc `/cdrom` |

#### MongoDB Step 3 - Trạng thái service

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 3 - Trạng thái service MongoDB (service_status)` |
| Thứ tự | `14` hoặc `3` nếu plan riêng |
| Host scope | `Host đầu tiên` |
| Lệnh systemctl | `systemctl status mongod` |
| Chuỗi yêu cầu | `active (running)` |

### 11.7 Nhập Step Hadoop - SIMO

#### Hadoop Step 1 - Thông số hệ thống

| Field | Giá trị nhập |
|-------|--------------|
| Hệ thống | `Hadoop - SIMO` |
| Step | `Bước 1 - Thông số hệ thống (system_info)` |
| Thứ tự | `15` hoặc `1` nếu plan riêng |
| Host scope | `Tất cả host` |
| Các lệnh | `date`, `hostname` |

#### Hadoop Step 2 - Phân vùng đĩa

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 2 - Phân vùng đĩa (disk_space)` |
| Thứ tự | `16` hoặc `2` nếu plan riêng |
| Host scope | `Tất cả host` |
| Lệnh kiểm tra | `df -kh` |
| Ngưỡng %used | `90` |
| Mount loại trừ | `/cdroom` hoặc `/cdrom` |

#### Hadoop Step 3 - HDFS Report

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 3 - HDFS dfsadmin report (hdfs_report)` |
| Thứ tự | `17` hoặc `3` nếu plan riêng |
| Host scope | `Host đầu tiên` |
| Lệnh HDFS | `hdfs dfsadmin -report` |
| Mẫu Safe Mode báo lỗi | `safe mode is ON` |
| Số live DataNode tối thiểu | `1` hoặc số DataNode thực tế mong muốn |
| Ngưỡng DFS Used % | `90` |

> Tài liệu yêu cầu kiểm tra trạng thái HDFS qua `hdfs dfsadmin -report`, chú ý Safe Mode và live datanode. Nếu cluster có 3 node, nên nhập `3` cho `Số live DataNode tối thiểu` khi cả 3 node phải online.

### 11.8 Nhập Step Application

Tạo các step tương tự cho từng system Application: WSO2 EI, WSO2 APIM, HAProxy, Kafka, App Servers.

#### Application Step 1 - Thông số hệ thống

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 1 - Thông số hệ thống (system_info)` |
| Host scope | `Tất cả host` |
| Các lệnh | `date`, `hostname` |

#### Application Step 2 - Phân vùng đĩa

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 2 - Phân vùng đĩa (disk_space)` |
| Host scope | `Tất cả host` |
| Lệnh kiểm tra | `df -kh` |
| Ngưỡng %used | `90` |
| Mount loại trừ | `/cdroom` hoặc `/cdrom` |

#### Application Step 3 - Service Status

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 3 - Service Status (service_status)` |
| Host scope | `Host đầu tiên` hoặc `Tất cả host` tùy service chạy trên mấy máy |
| Lệnh kiểm tra service | Nhập lệnh `systemctl status ...` tương ứng |
| Chuỗi yêu cầu | `active (running)` |

Theo tài liệu, với WSO2 APIM nhập:

```text
systemctl status wso2am-3.2.0.service
```

Với HAProxy, Kafka, Application, tài liệu chưa ghi rõ tên service. Người vận hành cần nhập đúng service thực tế, ví dụ:

```text
systemctl status haproxy
systemctl status kafka
systemctl status ten-service-ung-dung
```

#### Application Step 3b - Docker Containers

Chỉ tạo step này cho nhóm **Application Servers** nếu cần kiểm tra docker service ở trạng thái `n/n` như tài liệu yêu cầu.

| Field | Giá trị nhập |
|-------|--------------|
| Step | `Bước 3b - Docker containers (docker_containers)` |
| Host scope | `Tất cả host` |
| Lệnh docker ps | `docker ps --format '{{.Names}}\t{{.Status}}'` |
| Chuỗi đánh dấu container running | `Up` |
| Lệnh docker service ls (optional) | `docker service ls --format '{{.Name}}\t{{.Replicas}}'` |

### 11.9 Checklist trước khi bấm Run

Trước khi chạy plan, kiểm tra:

- **System:** Đã tạo đủ `oracle`, `mongodb`, `hadoop`, `application`.
- **Host:** Mỗi system đã có đúng IP trong tài liệu.
- **Oracle Extra JSON:** DB host có `oracle_sid` và `asm_sid`.
- **Step order:** Các step có thứ tự tăng dần, không bị trùng nếu muốn xem báo cáo theo đúng trình tự.
- **Host scope:** Step kiểm tra tất cả máy để `Tất cả host`; step chỉ cần chạy một máy để `Host đầu tiên`.
- **SQL:** Các SQL Oracle đã dán đầy đủ dấu `;`.
- **List field:** Mỗi giá trị nằm trên một dòng riêng, không nhập bằng dấu phẩy trên cùng một dòng.
- **Quyền SSH:** User SSH có quyền chạy `sqlplus`, `crs_stat`, `ocrcheck`, `crsctl`, `systemctl`, `docker` tương ứng.

---

## 12. Troubleshooting

### Container không khởi động được

```bash
docker logs simo_dailycheck --tail 50
```

**Lỗi phổ biến:**

| Lỗi | Nguyên nhân | Cách xử lý |
|-----|-------------|------------|
| `IsADirectoryError: config.yaml` | `config/config.yaml` bị tạo nhầm thành thư mục | `rm -rf config/config.yaml` rồi tạo lại file |
| `address already in use :5000` | macOS AirPlay dùng port 5000 | Đổi sang `5001:5000` trong `docker-compose.yml` |
| `No module named 'src'` | Chạy từ sai thư mục | Đảm bảo `docker compose` chạy từ thư mục gốc project |

### Plan báo lỗi khi run

Dashboard sẽ hiển thị danh sách lỗi cụ thể, ví dụ:

```
[Oracle SIMO / disk_space] Thiếu tham số 'threshold_pct'
[Oracle SIMO / alert_log] Host 'db1' chưa khai báo extra.oracle_sid
```

→ Vào **Plan Editor**, sửa step tương ứng và điền đầy đủ tham số bắt buộc.

### Kết nối SSH thất bại

- Kiểm tra SSH key được mount: `- ${HOME}/.ssh:/root/.ssh:ro` trong `docker-compose.yml`.
- Thử kết nối thủ công: `ssh -i ~/.ssh/id_rsa oracle@10.1.36.11`.
- Kiểm tra `ip`, `username`, `key_path` trong phần cấu hình Host.

### Xem log chi tiết

```bash
# Log container real-time
docker logs -f simo_dailycheck

# Log file trong container
docker exec simo_dailycheck tail -100 /app/logs/dailycheck.log
```

### Reset toàn bộ dữ liệu

```bash
docker compose down
rm -f data/dailycheck.db
docker compose up -d
```

> ⚠️ Lệnh trên xoá toàn bộ cấu hình systems/hosts/plans và lịch sử kết quả.

---

*SIMO Daily Check System – Phòng Phần mềm SIMO*
