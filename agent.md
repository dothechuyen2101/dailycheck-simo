# AGENT SPECIFICATION DOCUMENT - SIMO AUTOMATED DAILY CHECK (agent_en.md)

**Version:** 1.0
**Project:** SIMO Automated Daily Check Application
**Goal:** 100% automation of the SIMO system's Daily Check process, reducing the manual process that takes 45-60 minutes down to 0 minutes (fully automated).

---

## 1. AGENT ROLES AND RESPONSIBILITIES
The Agent acts as a "Virtual Operations Engineer", executing periodic system checks with absolute precision.
- **Execution Schedule:** Starts preparation at 07:55 AM and begins parallel execution of checks across all systems at exactly 08:00 AM every working day.
- **Data Analysis:** Collects results from servers and compares them with pre-configured thresholds.
- **Real-time Alert:** Sends immediate alerts via email/chat (Slack/Teams) upon detecting anomalies without waiting for the entire script to finish.
- **Summary Report:** Generates a visual HTML report and sends a summary email at 08:15 AM.
- **Historical Storage:** Saves all results into a history database (PostgreSQL) for trend analysis.

---

## 2. AGENT ARCHITECTURE AND CORE COMPONENTS
The Agent is built on a Layered Architecture model:

### 2.1. Application & Core Layer
- **Scheduler (APScheduler):** Triggers the Agent automatically on time.
- **Check Engine:** Engine for parallel execution of the check modules.
- **Alert Manager:** Anti-spam alert engine.
- **Report Generator:** Engine to render the HTML email report interfaces.

### 2.2. Connector Layer
- **SSH Connector:** Manages the SSH Pool connecting to servers via the Paramiko library.
- **Database Connectors:** `cx_Oracle` (Oracle DB), `PyMongo` (MongoDB).

---

## 3. CHECK MODULES
The Agent is pre-loaded with 4 check modules corresponding to the 4 components of the SIMO infrastructure:

### 3.1. Oracle DB Module (11 check steps)
Executes on the Oracle RAC cluster.
1. **System Info:** Check date/hostname to verify the connection.
2. **Disk Space:** Scan using `df -k`, alert if `%used >= 90%`.
3. **CRS Status:** Scan cluster status using `crs_stat -t`. Ensure services are online.
4. **OCR Check:** Verify integrity (expecting: integrity check succeeded).
5. **Voting Disk:** Ensure all 3 voting disks are in an ONLINE state.
6. **Archive Switch:** Execute SQL `alter system archive log current`.
7. **Free Tablespace:** Check via SQL query, ensuring the %Free of tablespaces is above the safety threshold.
8. **Unusable Indexes:** Verify it returns `no rows selected`.
9. **Invalid Objects:** Verify it returns `no rows selected`.
10. **ASM Disk Group:** Alert if `%used >= 90%`.
11. **Alert Log Check:** Scan the last 1000 lines of the Alert log for `ORA-`, `TNS-`, and `Errors` strings.

### 3.2. MongoDB Module (3 check steps)
Executes on the MongoDB cluster.
1. Verify Date/Hostname.
2. Check disk partition usage (Alert if `>= 90%`).
3. Run `systemctl status mongod` to ensure the service is `active (running)`.

### 3.3. Hadoop Module (3 check steps)
Executes on the Hadoop nodes.
1. Verify Date/Hostname.
2. Check disk partition usage.
3. Run `hdfs dfsadmin -report`, check the "Safe Mode" flag, and count the number of "Live DataNodes".

### 3.4. Application Module (3 check steps x 5 systems)
Applies to WSO2 EI, WSO2 APIM, HAProxy, Kafka, and Application Servers.
1. Verify Date/Hostname.
2. Check disk partition usage.
3. Check service status via `systemctl` and the status of Docker containers (Replica count).

---

## 4. TARGET SERVER INVENTORY

**Databases:**
- **Oracle RAC (19.3):** `10.1.36.11`, `10.1.36.12`
- **MongoDB Cluster:** `10.1.36.41`, `10.1.36.44`
- **Hadoop Cluster:** `10.1.36.31`, `10.1.36.34`, `10.1.36.37`

**Application Servers:**
- **WSO2 EI Cluster:** `10.1.46.31`, `10.1.46.32`
- **WSO2 APIM Cluster:** `10.1.16.11`, `10.1.16.12`
- **HAProxy Cluster:** `10.1.16.17`, `10.1.16.18`
- **Kafka Cluster:** `10.1.46.23`, `10.1.46.24`
- **Application Servers:** `10.1.46.41`, `10.1.46.42`, `10.1.46.43`

---

## 5. OPERATIONAL & SECURITY PARAMETERS
- **Target Completion Time (SLA):** Under 10 minutes. Target success rate `>= 99%`.
- **Credentials Security Policy:**
  - DO NOT store plaintext passwords in the codebase.
  - Prioritize using SSH Keys.
  - Use a Config Store (Vault/YAML encrypted) to hold sensitive parameters.
  - Grant Read-only access for the Agent User on Linux and SELECT privileges on `DBA_*` views for the Oracle User.
