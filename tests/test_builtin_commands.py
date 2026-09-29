import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from src.builtin_commands import BuiltinCommandSet, BuiltinCommandStore
from src.checks import app_checks, hadoop_checks, oracle_checks
from src.checks.common import parse_df_issues
from src.config_manager import load_config
from src.ssh_manager import CommandResult


class FakeClient:
    """Giả lập SSHClient: responder(command, input) -> (stdout, stderr, exit_code)."""

    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder or (lambda command, input: ("", "", 0))

    def run(self, command, timeout=None, input=None):
        self.calls.append((command, timeout, input))
        stdout, stderr, exit_code = self.responder(command, input)
        return CommandResult(stdout, stderr, exit_code, exit_code == 0, command)


ORACLE_CTX = {
    "host_name": "db1",
    "host_ip": "10.0.0.1",
    "instance": "simo1",
    "asm_instance": "+ASM1",
    "grid_home": "/u01/app/19.3.0/grid",
    "oracle_home": "/u01/app/oracle/product/19.3.0/dbhome_1",
    "oracle_user": "oracle",
    "db_unique_name": "",
}
SQLPLUS = (
    'su - oracle -c "export ORACLE_HOME=/u01/app/oracle/product/19.3.0/dbhome_1 ORACLE_SID={sid}; '
    '/u01/app/oracle/product/19.3.0/dbhome_1/bin/sqlplus -S / as sysdba"'
)


class BuiltinCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.yaml")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(
                "general:\n"
                "  timezone: Asia/Ho_Chi_Minh\n"
                "  command_timeout: 60\n"
                f"  data_dir: {self.tmpdir}\n"
                "security:\n"
                "  encryption_key: test-secret\n"
                "oracle:\n"
                "  hosts:\n"
                "    - name: db1\n"
                "      ip: 10.0.0.1\n"
                "      instance: simo1\n"
                "hadoop:\n"
                "  namenode_ip: 10.0.0.9\n"
                "  nodes:\n"
                "    - name: hd1\n"
                "      ip: 10.0.0.9\n"
            )
        load_config(self.config_path)
        self.store = BuiltinCommandStore()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _step(self, key):
        return next(step for step in self.store.list_steps() if step["key"] == key)

    def _override_rows(self):
        with sqlite3.connect(self.store.db_path) as conn:
            return conn.execute("SELECT step_key FROM builtin_step_overrides").fetchall()

    # ── Store & validate ──────────────────────────────────────────────────────

    def test_defaults_without_override(self):
        step = self._step("oracle.disk_space")
        self.assertEqual(step["commands"][0]["command"], "df -k")
        self.assertEqual(step["commands"][0]["timeout_sec"], 60)
        self.assertFalse(step["is_modified"])
        self.assertTrue(step["enabled"])

    def test_save_and_reset_override(self):
        step = self.store.save_override("oracle.crs_status", {
            "enabled": True,
            "commands": {"main": {"command": " /opt/grid/bin/crs_stat -t ", "timeout_sec": "45"}},
        })
        main = step["commands"][0]
        self.assertTrue(step["is_modified"])
        self.assertEqual(main["command"], "/opt/grid/bin/crs_stat -t")
        self.assertEqual(main["timeout_sec"], 45)
        self.assertEqual(main["default"]["command"], "{{grid_home}}/bin/crsctl stat res -t")

        resolved = self.store.load_command_set().resolve("oracle.crs_status", "main")
        self.assertEqual(resolved["command"], "/opt/grid/bin/crs_stat -t")

        step = self.store.reset("oracle.crs_status")
        self.assertFalse(step["is_modified"])
        self.assertEqual(step["commands"][0]["command"], "{{grid_home}}/bin/crsctl stat res -t")
        self.assertEqual(self._override_rows(), [])

    def test_values_equal_to_default_are_not_stored(self):
        default = self._step("oracle.free_tablespace")["commands"][0]
        step = self.store.save_override("oracle.free_tablespace", {
            "enabled": True,
            "commands": {"main": {
                "command": default["command"],
                "script": default["script"].replace("\n", "\r\n") + "\n",
                "timeout_sec": default["timeout_sec"],
            }},
        })
        self.assertFalse(step["is_modified"])
        self.assertEqual(self._override_rows(), [])

    def test_disabled_step_is_persisted(self):
        step = self.store.save_override("oracle.ocr", {"enabled": False, "commands": {}})
        self.assertFalse(step["enabled"])
        self.assertTrue(step["is_modified"])
        self.assertFalse(self.store.load_command_set().is_enabled("oracle.ocr"))
        self.assertTrue(self.store.load_command_set().is_enabled("oracle.voting_disk"))

    def test_validation_rejects_bad_input(self):
        bad_payloads = [
            {"commands": {"main": {"command": "df -k $(id)"}}},
            {"commands": {"main": {"command": "df -k\nrm -rf /tmp/x"}}},
            {"commands": {"main": {"command": "sudo df -k"}}},
            {"commands": {"main": {"command": "df -k {{unknown_var}}"}}},
            {"commands": {"main": {"command": "   "}}},
            {"commands": {"main": {"command": "df -k", "timeout_sec": "0"}}},
            {"commands": {"main": {"command": "df -k", "timeout_sec": "abc"}}},
            {"commands": {"main": {"command": "df -k", "script": "select 1 from dual;"}}},
            {"commands": {"other": {"command": "df -k"}}},
            {"commands": "df -k"},
            None,
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.store.save_override("oracle.disk_space", payload)
        with self.assertRaises(ValueError):
            self.store.save_override("oracle.asm_diskgroup", {"commands": {"main": {"script": "  "}}})
        with self.assertRaises(KeyError):
            self.store.save_override("oracle.not_a_step", {"commands": {}})
        self.assertEqual(self._override_rows(), [])

    def test_step_specific_variable_is_allowed(self):
        step = self.store.save_override("oracle.alert_log", {
            "commands": {"scan": {"command": "tail -2000 {{alert_dir}}/alert_{{instance}}.log"}},
        })
        self.assertEqual(step["commands"][1]["command"], "tail -2000 {{alert_dir}}/alert_{{instance}}.log")
        with self.assertRaises(ValueError):
            self.store.save_override("oracle.disk_space", {
                "commands": {"main": {"command": "df -k {{alert_dir}}"}},
            })

    def test_optional_fallback_can_be_blank(self):
        self.store.save_override("oracle.crs_status", {"commands": {"fallback": {"command": ""}}})
        self.assertFalse(self.store.load_command_set().has_command("oracle.crs_status", "fallback"))

    # ── Render & run ──────────────────────────────────────────────────────────

    def test_run_renders_variables_and_sends_sql_via_stdin(self):
        client = FakeClient()
        commands = BuiltinCommandSet()
        commands.run(client, "oracle.asm_diskgroup", "main", {**ORACLE_CTX, "instance": "simo2"})
        command, timeout, stdin = client.calls[0]
        self.assertEqual(command, SQLPLUS.format(sid="simo2"))
        self.assertEqual(timeout, 60)
        self.assertIn("from v$asm_diskgroup;", stdin)
        self.assertTrue(stdin.endswith("EXIT;\n"))

    def test_grid_commands_use_grid_home(self):
        client = FakeClient()
        commands = BuiltinCommandSet()
        ctx = {**ORACLE_CTX, "grid_home": "/u01/app/19.0.0/grid"}
        commands.run(client, "oracle.ocr", "main", ctx)
        commands.run(client, "oracle.voting_disk", "main", ctx)
        commands.run(client, "oracle.crs_status", "main", ctx)
        self.assertEqual([c[0] for c in client.calls], [
            "/u01/app/19.0.0/grid/bin/ocrcheck",
            "/u01/app/19.0.0/grid/bin/crsctl query css votedisk",
            "/u01/app/19.0.0/grid/bin/crsctl stat res -t",
        ])

    def test_su_is_allowed_only_in_builtin_commands(self):
        from src.dynamic_modules import render_command_template

        step = self.store.save_override("oracle.archive_switch", {
            "commands": {"main": {"command": 'su - oracle -c "export ORACLE_SID={{instance}}; sqlplus -s / as sysdba"'}},
        })
        self.assertTrue(step["is_modified"])
        with self.assertRaises(ValueError):
            self.store.save_override("oracle.archive_switch", {
                "commands": {"main": {"command": "sudo -u oracle sqlplus -s / as sysdba"}},
            })
        with self.assertRaises(ValueError):
            render_command_template("su - oracle -c id", {})

    def test_run_without_script_sends_no_stdin(self):
        client = FakeClient()
        BuiltinCommandSet().run(client, "oracle.disk_space", "main", ORACLE_CTX)
        self.assertEqual(client.calls, [("df -k", 60, None)])

    def test_run_rejects_unsafe_variable_value(self):
        client = FakeClient()
        res = BuiltinCommandSet().run(
            client, "oracle.archive_switch", "main", {**ORACLE_CTX, "instance": "simo1; reboot"}
        )
        self.assertFalse(res.success)
        self.assertIn("Lệnh không hợp lệ", res.stderr)
        self.assertEqual(client.calls, [])

    def test_run_ignores_unused_context_values(self):
        client = FakeClient()
        res = BuiltinCommandSet().run(
            client, "application.disk_space", "main",
            {"host_name": "app1", "host_ip": "10.0.0.5", "app_name": "Kafka (prod)", "service_name": "kafka"},
        )
        self.assertTrue(res.success)
        self.assertEqual(client.calls[0][0], "df -kh")

    def test_override_is_used_at_run_time(self):
        self.store.save_override("oracle.disk_space", {"commands": {"main": {"command": "df -kP", "timeout_sec": 15}}})
        client = FakeClient()
        self.store.load_command_set().run(client, "oracle.disk_space", "main", ORACLE_CTX)
        self.assertEqual(client.calls, [("df -kP", 15, None)])

    # ── Parser & checks ───────────────────────────────────────────────────────

    def test_parse_df_issues_handles_df_variants(self):
        df_t = (
            "Filesystem     Type    1K-blocks    Used Available Use% Mounted on\n"
            "/dev/sda1      ext4     10000000 9500000    500000  95% /\n"
            "tmpfs          tmpfs     1000000       0   1000000   0% /dev/shm\n"
            "/dev/mapper/very-long-volume-group-name\n"
            "               xfs      20000000 2000000  18000000  10% /data\n"
            "/dev/sr0       iso9660      1000    1000         0 100% /cdrom\n"
        )
        self.assertEqual(parse_df_issues(df_t, 90), ["/ (95%)"])

        df_kh = (
            "Filesystem      Size  Used Avail Use% Mounted on\n"
            "/dev/sda1        50G   20G   30G  40% /\n"
            "/dev/mapper/vg_data-lv_u01_with_a_long_name\n"
            "                200G  184G   16G  92% /u01\n"
            "/dev/sdb1       100G   91G    9G  91% /u02\n"
        )
        self.assertEqual(parse_df_issues(df_kh, 90), ["/u01 (92%)", "/u02 (91%)"])

    def test_parse_df_issues_reads_aix_used_column(self):
        # AIX df -k: cột áp chót là %Iused (inode), phải đọc cột %Used
        df_aix = (
            "Filesystem    1024-blocks      Free %Used    Iused %Iused Mounted on\n"
            "/dev/hd4          2097152    104857   95%     8123     3% /\n"
            "/dev/hd2          6291456   3145728   50%    60000    92% /usr\n"
            "/proc                   -         -    -         -     -  /proc\n"
            "/dev/fslv00     104857600  10485760   91%     1200     1% /u01\n"
        )
        self.assertEqual(parse_df_issues(df_aix, 90), ["/ (95%)", "/u01 (91%)"])

    def test_parse_df_issues_excludes_docker_and_configured_mounts(self):
        df_kh = (
            "Filesystem      Size  Used Avail Use% Mounted on\n"
            "/dev/sda1        50G   49G    1G  98% /\n"
            "overlay          50G   49G    1G  98% /var/lib/docker/overlay2/298dfb669f4c/merged\n"
            "shm              64M     0   64M 100% /var/lib/docker/containers/abc/mounts/shm\n"
            "10.1.1.23:/mail 100G   99G    1G  99% /mailbox\n"
            "/dev/sdb1       100G   99G    1G  99% /data/simo/dockerswarm\n"
        )
        self.assertEqual(
            parse_df_issues(df_kh, 90, ["/mailbox"]),
            ["/ (98%)", "/data/simo/dockerswarm (99%)"],
        )

    def test_sql_errors_are_reported_as_error(self):
        client = FakeClient(lambda command, stdin: ("ERROR:\nORA-01034: ORACLE not available", "", 0))
        result = oracle_checks.check_free_tablespace(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "ERROR")
        self.assertIn("ORA-01034", result.message)

        client = FakeClient(lambda command, stdin: ("SP2-0310: unable to open file", "", 0))
        result = oracle_checks.check_asm_diskgroup(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "ERROR")

        client = FakeClient(lambda command, stdin: ("", "bash: sqlplus: command not found", 127))
        result = oracle_checks.check_invalid_objects(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "ERROR")

    def test_tablespace_and_asm_thresholds(self):
        tablespace_output = (
            "Tablespace Name                Total MBy Used Mbyt Free Mbyt  % Free\n"
            "------------------------------ --------- --------- --------- -------\n"
            "SYSTEM                              1000       900       100   10.00\n"
            "USERS                               1000       950        50    5.00\n"
        )
        client = FakeClient(lambda command, stdin: (tablespace_output, "", 0))
        result = oracle_checks.check_free_tablespace(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "CRITICAL")
        self.assertIn("USERS", result.message)
        self.assertNotIn("SYSTEM", result.message)

        asm_output = (
            "NAME                             TOTAL_MB    FREE_MB      %used\n"
            "------------------------------ ---------- ---------- ----------\n"
            "DATA                               100000       5000         95\n"
            "FRA                                100000      50000         50\n"
        )
        client = FakeClient(lambda command, stdin: (asm_output, "", 0))
        result = oracle_checks.check_asm_diskgroup(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "CRITICAL")
        self.assertIn("DATA", result.message)
        self.assertNotIn("FRA", result.message)

    def test_alert_log_unreadable_is_error(self):
        def responder(command, stdin):
            if stdin and "background_dump_dest" in stdin:
                return ("background_dump_dest string /u01/diag/rdbms/simo/simo1/trace", "", 0)
            return ("", "tail: cannot open '/u01/diag/rdbms/simo/simo1/trace/alert_simo1.log'", 1)

        client = FakeClient(responder)
        result = oracle_checks.check_alert_log(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(client.calls[1][0], "tail -1000 /u01/diag/rdbms/simo/simo1/trace/alert_simo1.log")

    def test_alert_log_filters_keywords(self):
        log = "Thread 1 advanced\nORA-00600: internal error\nCompleted checkpoint\nTNS-12535: timeout\n"

        def responder(command, stdin):
            if stdin:
                return ("", "", 0)
            return (log, "", 0)

        client = FakeClient(responder)
        result = oracle_checks.check_alert_log(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "WARNING")
        self.assertEqual(result.details["error_lines"], ["ORA-00600: internal error", "TNS-12535: timeout"])
        # Không lấy được background_dump_dest → diag/rdbms/<db_unique_name>/<instance>/trace
        self.assertEqual(
            client.calls[1][0],
            "tail -1000 /u01/app/oracle/diag/rdbms/simo/simo1/trace/alert_simo1.log",
        )

        client = FakeClient(responder)
        oracle_checks.check_alert_log(client, "db1", BuiltinCommandSet(), {**ORACLE_CTX, "db_unique_name": "SIMODB"})
        self.assertEqual(
            client.calls[1][0],
            "tail -1000 /u01/app/oracle/diag/rdbms/simodb/simo1/trace/alert_simo1.log",
        )

    def test_crs_fallback_runs_when_command_not_found(self):
        healthy = "Name           Type           Target    State     Host\n------\nora.asm        ora.asm.type   ONLINE    ONLINE    db1\n"

        def responder(command, stdin):
            if command.endswith("crsctl stat res -t"):
                return ("", "ksh: /u01/app/19.3.0/grid/bin/crsctl:  not found.", 127)
            return (healthy, "", 0)

        client = FakeClient(responder)
        result = oracle_checks.check_crs_status(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "OK")
        self.assertEqual(client.calls[0][0], "/u01/app/19.3.0/grid/bin/crsctl stat res -t")
        self.assertEqual(client.calls[1][0], "crs_stat -t")

    def test_parse_crs_issues_reads_crsctl_stat_res_t(self):
        output = (
            "--------------------------------------------------------------------------------\n"
            "Name           Target  State        Server                   State details\n"
            "--------------------------------------------------------------------------------\n"
            "Local Resources\n"
            "--------------------------------------------------------------------------------\n"
            "ora.LISTENER.lsnr\n"
            "               ONLINE  ONLINE       simo-db01                STABLE\n"
            "               ONLINE  OFFLINE      simo-db02                STABLE\n"
            "ora.proxy_advm\n"
            "               OFFLINE OFFLINE      simo-db01                STABLE\n"
            "--------------------------------------------------------------------------------\n"
            "Cluster Resources\n"
            "--------------------------------------------------------------------------------\n"
            "ora.ASMNET1LSNR_ASM.lsnr(ora.asmgroup)\n"
            "      1        ONLINE  ONLINE       simo-db01                STABLE\n"
            "      3        OFFLINE OFFLINE                               STABLE\n"
            "ora.simo.db\n"
            "      1        ONLINE  ONLINE       simo-db01                Open,HOME=/u01/app/o\n"
            "                                                             racle/product/19.3.0\n"
            "                                                             /dbhome_1,STABLE\n"
            "      2        ONLINE  INTERMEDIATE simo-db02                Mounted (Closed),STABLE\n"
            "ora.gsd\n"
            "      1        ONLINE  OFFLINE                               STABLE\n"
        )
        issues = oracle_checks.parse_crs_issues(output, ["ora.gsd"])
        self.assertEqual(issues, [
            "ora.LISTENER.lsnr (target=ONLINE, state=OFFLINE, server=simo-db02)",
            "ora.simo.db (target=ONLINE, state=INTERMEDIATE, server=simo-db02)",
        ])

    def test_parse_crs_issues_reads_crs_stat_t(self):
        output = (
            "Name           Type           Target    State     Host\n"
            "------------------------------------------------------------\n"
            "ora.DATA.dg    ora....up.type ONLINE    ONLINE    hcmcdb1\n"
            "ora.gsd        ora.gsd.type   OFFLINE   OFFLINE\n"
            "ora....bkup.db ora....se.type OFFLINE   OFFLINE\n"
            "ora.cvu        ora.cvu.type   ONLINE    OFFLINE\n"
        )
        self.assertEqual(
            oracle_checks.parse_crs_issues(output, ["ora.gsd", "ora....bkup.db"]),
            ["ora.cvu (target=ONLINE, state=OFFLINE)"],
        )

    def test_docker_parse_accepts_tab_or_space(self):
        def responder(command, stdin):
            if command.startswith("docker ps"):
                return ("web\tUp 2 hours\ndb Exited (1) 3 minutes ago", "", 0)
            return ("", "This node is not a swarm manager.", 1)

        client = FakeClient(responder)
        ctx = {"host_name": "app1", "host_ip": "10.0.0.5", "app_name": "API", "service_name": ""}
        result = app_checks.check_docker_containers(client, "API", "app1", BuiltinCommandSet(), ctx)
        self.assertEqual(result.status, "WARNING")
        self.assertEqual(result.details["issues"], ["db (Exited (1) 3 minutes ago)"])

    def test_run_all_skips_disabled_steps(self):
        self.store.save_override("oracle.ocr", {"enabled": False})
        self.store.save_override("oracle.alert_log", {"enabled": False})
        client = FakeClient()

        @contextmanager
        def fake_connect(host_cfg):
            yield client

        with patch("src.checks.oracle_checks.ssh_connect", fake_connect):
            results = oracle_checks.run_all(self.store.load_command_set())

        names = [r.check_name for r in results]
        self.assertEqual(len(results), 9)
        self.assertNotIn("Bước 4 – OCR Check", names)
        self.assertNotIn("Bước 11 – Alert Log", names)

    def test_host_with_all_steps_disabled_is_not_contacted(self):
        for key in ("hadoop.system_info", "hadoop.disk_space", "hadoop.hdfs_report"):
            self.store.save_override(key, {"enabled": False})
        with patch("src.checks.hadoop_checks.ssh_connect") as connect:
            results = hadoop_checks.run_all(self.store.load_command_set())
        self.assertEqual(results, [])
        connect.assert_not_called()


AIX_PS = """    USER COMMAND
    root /u01/app/19.0.0/grid/bin/ohasd.bin reboot
    grid /u01/app/19.0.0/grid/bin/ocssd.bin
  oracle ora_pmon_simo1
    grid asm_pmon_+ASM1
"""
ORATAB = """# This file is used by ORACLE utilities.
+ASM1:/u01/app/19.0.0/grid:N		# line added by Agent
simo:/u01/app/oracle/product/19.0.0/dbhome_1:N		# line added by Agent
"""


class OracleEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.yaml")
        self._write_config("")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_config(self, oracle_extra):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(
                "general:\n"
                "  command_timeout: 60\n"
                f"  data_dir: {self.tmpdir}\n"
                "oracle:\n"
                f"{oracle_extra}"
                "  hosts:\n"
                "    - {name: DB1, ip: 10.1.36.11, instance: simo1}\n"
                "    - {name: DB2, ip: 10.1.36.12, instance: simo2}\n"
            )
        load_config(self.config_path)

    def test_parse_environment_from_processes_and_oratab(self):
        env = oracle_checks.parse_oracle_environment(AIX_PS, ORATAB, "simo1")
        self.assertEqual(env, {
            "grid_home": "/u01/app/19.0.0/grid",
            "oracle_user": "oracle",
            "oracle_home": "/u01/app/oracle/product/19.0.0/dbhome_1",
        })
        # Không có tiến trình clusterware: lấy grid home từ dòng +ASM trong oratab
        env = oracle_checks.parse_oracle_environment("", ORATAB, "simo1")
        self.assertEqual(env["grid_home"], "/u01/app/19.0.0/grid")
        self.assertNotIn("oracle_user", env)
        # oratab không có tên DB khớp nhưng chỉ có 1 database
        env = oracle_checks.parse_oracle_environment("", "+ASM1:/g:N\nprod:/db/home:N\n", "simo1")
        self.assertEqual(env["oracle_home"], "/db/home")

    def _run_oracle(self, responder):
        clients = []

        @contextmanager
        def fake_connect(host_cfg):
            clients.append(FakeClient(responder))
            yield clients[-1]

        with patch("src.checks.oracle_checks.ssh_connect", fake_connect):
            results = oracle_checks.run_all(BuiltinCommandSet())
        return results, clients

    def test_run_all_uses_discovered_environment_on_first_node(self):
        def responder(command, stdin):
            if command == "ps -eo user,args":
                return AIX_PS, "", 0
            if command == "cat /etc/oratab":
                return ORATAB, "", 0
            return "", "", 0

        _, (db1, db2) = self._run_oracle(responder)
        db1_commands = [c[0] for c in db1.calls]
        self.assertIn("/u01/app/19.0.0/grid/bin/crsctl stat res -t", db1_commands)
        self.assertIn(
            'su - oracle -c "export ORACLE_HOME=/u01/app/oracle/product/19.0.0/dbhome_1 ORACLE_SID=simo1; '
            '/u01/app/oracle/product/19.0.0/dbhome_1/bin/sqlplus -S / as sysdba"',
            db1_commands,
        )
        # Node 2 chỉ chạy bước 1–2, không cần dò môi trường
        self.assertNotIn("ps -eo user,args", [c[0] for c in db2.calls])

    def test_config_overrides_discovery(self):
        self._write_config(
            "  grid_home: /opt/grid\n"
            "  oracle_home: /opt/db\n"
            "  oracle_user: oradb\n"
        )
        _, (db1, _) = self._run_oracle(lambda command, stdin: ("", "", 0))
        db1_commands = [c[0] for c in db1.calls]
        self.assertNotIn("ps -eo user,args", db1_commands)
        self.assertIn("/opt/grid/bin/ocrcheck", db1_commands)
        self.assertIn(
            'su - oradb -c "export ORACLE_HOME=/opt/db ORACLE_SID=simo1; /opt/db/bin/sqlplus -S / as sysdba"',
            db1_commands,
        )

    def test_missing_environment_is_reported_clearly(self):
        results, (db1, _) = self._run_oracle(lambda command, stdin: ("", "", 0))
        by_name = {r.check_name: r for r in results if r.host == "DB1"}
        self.assertEqual(by_name["Bước 4 – OCR Check"].status, "ERROR")
        self.assertIn("oracle.grid_home", by_name["Bước 4 – OCR Check"].message)
        self.assertEqual(by_name["Bước 7 – Free Tablespace"].status, "ERROR")
        self.assertIn("oracle.oracle_home", by_name["Bước 7 – Free Tablespace"].message)
        self.assertFalse(any(c[0].startswith("su -") for c in db1.calls))

    CRS_DB_RESOURCES = """NAME=ora.mid.db
TYPE=ora.database.type
ACL=owner:oracle:rwx,pgrp:oinstall:r--,other::r--
DB_UNIQUE_NAME=mid
ORACLE_HOME=/u01/app/oracle/product/19c/dbhome_2
USR_ORA_INST_NAME@SERVERNAME(simo-db01)=mid1
USR_ORA_INST_NAME@SERVERNAME(simo-db02)=mid2

NAME=ora.simo.db
TYPE=ora.database.type
ACL=owner:oracle:rwx,pgrp:oinstall:r--,other::r--
DB_UNIQUE_NAME=simo
ORACLE_HOME=/u01/app/oracle/product/19c/dbhome_1
USR_ORA_INST_NAME@SERVERNAME(simo-db01)=simo1
USR_ORA_INST_NAME@SERVERNAME(simo-db02)=simo2
"""

    def test_parse_crs_database_resources_picks_database_of_instance(self):
        env = oracle_checks.parse_crs_database_resources(self.CRS_DB_RESOURCES, "simo1")
        self.assertEqual(env, {
            "oracle_home": "/u01/app/oracle/product/19c/dbhome_1",
            "db_unique_name": "simo",
            "oracle_user": "oracle",
        })
        self.assertEqual(oracle_checks.parse_crs_database_resources(self.CRS_DB_RESOURCES, "mid2")["oracle_home"],
                         "/u01/app/oracle/product/19c/dbhome_2")
        self.assertEqual(oracle_checks.parse_crs_database_resources(self.CRS_DB_RESOURCES, "other1"), {})

    def test_oracle_home_from_crs_when_oratab_missing(self):
        ps = "  oracle ora_pmon_simo1\n    grid /u01/app/19c/grid/bin/ocssd.bin\n  oracle ora_pmon_mid1\n"

        def responder(command, stdin):
            if command == "ps -eo user,args":
                return ps, "", 0
            if command == "cat /etc/oratab":
                return "", "cat: 0652-050 Cannot open /etc/oratab.", 2
            if command == '/u01/app/19c/grid/bin/crsctl stat res -p -w "TYPE = ora.database.type"':
                return self.CRS_DB_RESOURCES, "", 0
            return "", "", 0

        _, (db1, _) = self._run_oracle(responder)
        self.assertIn(
            'su - oracle -c "export ORACLE_HOME=/u01/app/oracle/product/19c/dbhome_1 ORACLE_SID=simo1; '
            '/u01/app/oracle/product/19c/dbhome_1/bin/sqlplus -S / as sysdba"',
            [c[0] for c in db1.calls],
        )

    def test_crs_ignores_unplaced_cardinality_slot(self):
        output = (
            "Name           Target  State        Server                   State details\n"
            "--------------------------------------------------------------------------------\n"
            "ora.ASMNET1LSNR_ASM.lsnr(ora.asmgroup)\n"
            "      1        ONLINE  ONLINE       simo-db01                STABLE\n"
            "      2        ONLINE  ONLINE       simo-db02                STABLE\n"
            "      3        ONLINE  OFFLINE                               STABLE\n"
            "ora.simo.db\n"
            "      1        ONLINE  ONLINE       simo-db01                Open,STABLE\n"
            "      2        ONLINE  OFFLINE                               Instance Shutdown,STABLE\n"
        )
        # Slot 3 của asmgroup là slot thừa; instance 2 của DB bị tắt thì vẫn phải báo
        self.assertEqual(oracle_checks.parse_crs_issues(output, []),
                         ["ora.simo.db (target=ONLINE, state=OFFLINE)"])

    def test_voting_disk_message_shows_located_count(self):
        client = FakeClient(lambda command, stdin: (
            "##  STATE    File Universal Id                File Name Disk group\n"
            " 1. ONLINE   751e91a6d3c54f93bf2d0eda95d492d9 (/dev/rhdisk5) [OCR]\n"
            "Located 1 voting disk(s).", "", 0))
        result = oracle_checks.check_voting_disk(client, "DB1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "CRITICAL")
        self.assertIn("1/1 voting disk ONLINE (yêu cầu 3)", result.message)
        self.assertIn("EXTERNAL redundancy", result.message)

    def test_crs_error_shows_main_and_fallback(self):
        client = FakeClient(lambda command, stdin: ("", f"ksh: {command.split()[0]}: not found.", 127))
        result = oracle_checks.check_crs_status(client, "DB1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "ERROR")
        self.assertIn("/u01/app/19.3.0/grid/bin/crsctl stat res -t thất bại", result.message)
        self.assertIn("lệnh dự phòng crs_stat -t thất bại", result.message)

    def test_alert_log_summarizes_codes_and_ignores_keywords(self):
        self._write_config("")
        with open(self.config_path, "a", encoding="utf-8") as handle:
            handle.write("thresholds:\n  alert_log_ignore_keywords: ['TNS-12535']\n")
        load_config(self.config_path)
        log = (
            "Fatal NI connect error 12170.\n"
            "    TNS-12535: TNS:operation timed out\n" * 3
            + "ORA-00060: Deadlock detected\nORA-00060: Deadlock detected\nORA-01555: snapshot too old\n"
        )
        client = FakeClient(lambda command, stdin: ("", "", 0) if stdin else (log, "", 0))
        result = oracle_checks.check_alert_log(client, "DB1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "WARNING")
        self.assertEqual(result.message, "Phát hiện 3 dòng lỗi trong alert log: ORA-00060 ×2, ORA-01555 ×1")


class ServiceCheckTests(unittest.TestCase):
    APP_CTX = {"host_name": "app1", "host_ip": "10.0.0.5", "app_name": "API", "service_name": "wso2am"}

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        config_path = os.path.join(self.tmpdir, "config.yaml")
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(
                "general:\n"
                "  command_timeout: 60\n"
                f"  data_dir: {self.tmpdir}\n"
                "applications:\n"
                "  - name: Application Server\n"
                "    service: ngs-simo_simo-restapi\n"
                "    service_type: docker\n"
                "    hosts:\n"
                "      - {name: be1, ip: 10.0.0.41}\n"
                "      - {name: fe1, ip: 10.0.0.42, service: ''}\n"
                "  - name: WSO2\n"
                "    service: wso2am\n"
                "    hosts:\n"
                "      - {name: apim1, ip: 10.0.0.11}\n"
            )
        load_config(config_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _systemd(self, stdout, stderr="", exit_code=0):
        client = FakeClient(lambda command, stdin: (stdout, stderr, exit_code))
        return app_checks.check_service(client, "WSO2", "apim1", BuiltinCommandSet(), self.APP_CTX)

    def test_systemd_states(self):
        result = self._systemd("", "Unit wso2am.service could not be found.", 4)
        self.assertEqual(result.status, "ERROR")
        self.assertIn("Không tìm thấy service wso2am", result.message)

        result = self._systemd(
            "● wso2am.service - WSO2\n   Loaded: loaded\n"
            "   Active: activating (auto-restart) (Result: exit-code) since Tue 2026-09-29\n", "", 3)
        self.assertEqual(result.status, "WARNING")
        self.assertIn("activating (auto-restart)", result.message)

        result = self._systemd(
            "   Active: active (running) since Mon 2026-09-28\n"
            "Sep 28 10:00:00 apim1 wso2server.sh[1]: login failed for user admin\n")
        self.assertEqual(result.status, "OK")

        result = self._systemd("   Active: failed (Result: exit-code) since Tue 2026-09-29\n", "", 3)
        self.assertEqual(result.status, "CRITICAL")
        self.assertIn("failed (Result: exit-code)", result.message)

    def test_missing_unit_lists_similar_units(self):
        def responder(command, stdin):
            if command.startswith("systemctl status"):
                return "", "Unit wso2am-3.2.0.service could not be found.", 4
            if command.startswith("systemctl list-unit-files"):
                return "wso2mi-4.2.0.service  enabled\nhaproxy.service  disabled\n", "", 0
            return "", "", 0

        client = FakeClient(responder)
        ctx = {**self.APP_CTX, "service_name": "wso2am-3.2.0.service"}
        result = app_checks.check_service(client, "WSO2 EI", "ei1", BuiltinCommandSet(), ctx)
        self.assertEqual(result.status, "ERROR")
        self.assertIn("service có tên gần giống trên host: wso2mi-4.2.0.service", result.message)

    def test_active_exited_checks_process(self):
        status = "   Active: active (exited) since Thu 2026-07-30 10:29:00 +07; 2 months 0 days ago\n"

        def responder_with(processes):
            def responder(command, stdin):
                if command.startswith("systemctl status"):
                    return status, "", 0
                return processes, "", 0
            return responder

        ctx = {**self.APP_CTX, "service_name": "wso2am-3.2.0.service"}
        running = "  PID COMMAND\n 4242 /usr/bin/java -Dcarbon.home=/opt/wso2am-3.2.0 org.wso2.carbon.bootstrap.Bootstrap\n"
        result = app_checks.check_service(FakeClient(responder_with(running)), "APIM", "apim2", BuiltinCommandSet(), ctx)
        self.assertEqual(result.status, "OK")
        self.assertIn("PID 4242", result.message)

        result = app_checks.check_service(FakeClient(responder_with("  PID COMMAND\n 1 /sbin/init\n")),
                                          "APIM", "apim2", BuiltinCommandSet(), ctx)
        self.assertEqual(result.status, "WARNING")

    def test_process_service_type(self):
        ps = ("  PID COMMAND\n    1 /sbin/init\n"
              " 3100 /usr/lib/jvm/java/bin/java -Dcarbon.home=/opt/wso2mi-4.2.0 org.wso2.micro.integrator.bootstrap.Bootstrap\n")
        ctx = {**self.APP_CTX, "service_name": "wso2"}
        result = app_checks.check_process_service(FakeClient(lambda c, i: (ps, "", 0)), "EI", "ei1", BuiltinCommandSet(), ctx)
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.check_name, "Bước 3 – Tiến trình wso2")
        self.assertIn("PID 3100", result.message)

        result = app_checks.check_process_service(
            FakeClient(lambda c, i: ("  PID COMMAND\n    1 /sbin/init\n", "", 0)), "EI", "ei1", BuiltinCommandSet(), ctx)
        self.assertEqual(result.status, "CRITICAL")

    def test_active_exited_uses_process_pattern(self):
        def responder(command, stdin):
            if command.startswith("systemctl status"):
                return "   Active: active (exited) since Thu 2026-07-30 10:29:00 +07\n", "", 0
            return "  PID COMMAND\n 777 java -Dcarbon.home=/opt/wso2/apim org.wso2.carbon.bootstrap.Bootstrap\n", "", 0

        ctx = {**self.APP_CTX, "service_name": "wso2am-3.2.0.service"}
        result = app_checks.check_service(FakeClient(responder), "APIM", "apim2", BuiltinCommandSet(), ctx)
        self.assertEqual(result.status, "WARNING")
        self.assertIn("process_pattern", result.message)
        ctx["process_pattern"] = "org.wso2.carbon.bootstrap.Bootstrap"
        result = app_checks.check_service(FakeClient(responder), "APIM", "apim2", BuiltinCommandSet(), ctx)
        self.assertEqual(result.status, "OK")

    def _docker_service(self, service_ls, ps=("", "", 0)):
        def responder(command, stdin):
            return service_ls if command.startswith("docker service ls") else ps

        client = FakeClient(responder)
        ctx = {**self.APP_CTX, "service_name": "ngs-simo_simo-restapi"}
        return client, app_checks.check_docker_service(client, "App", "be1", BuiltinCommandSet(), ctx)

    def test_docker_service_on_manager_checks_replicas(self):
        client, result = self._docker_service(("ngs-simo_simo-restapi\t2/2\nngs-simo_simo-restapi-v2\t0/1", "", 0))
        self.assertEqual(result.status, "OK")
        self.assertEqual(
            client.calls[0][0],
            "docker service ls --filter name=ngs-simo_simo-restapi --format '{{.Name}}\\t{{.Replicas}}'",
        )
        _, result = self._docker_service(("ngs-simo_simo-restapi\t1/2", "", 0))
        self.assertEqual(result.status, "WARNING")
        _, result = self._docker_service(("ngs-simo_simo-restapi\t0/1", "", 0))
        self.assertEqual(result.status, "CRITICAL")

    def test_docker_service_on_worker_checks_local_containers(self):
        not_manager = ("", "Error response from daemon: This node is not a swarm manager.", 1)
        _, result = self._docker_service(not_manager, ("ngs-simo_simo-restapi.1.abc\tUp 3 days", "", 0))
        self.assertEqual(result.status, "OK")
        # Swarm tự phân bổ replica: worker không chạy container nào vẫn OK
        _, result = self._docker_service(not_manager, ("", "", 0))
        self.assertEqual(result.status, "OK")
        _, result = self._docker_service(("", "", 0), ("", "", 0))
        self.assertEqual(result.status, "CRITICAL")

    def test_run_all_uses_service_type_and_host_override(self):
        client = FakeClient(lambda command, stdin: ("   Active: active (running)", "", 0))

        @contextmanager
        def fake_connect(host_cfg):
            yield client

        with patch("src.checks.app_checks.ssh_connect", fake_connect):
            results = app_checks.run_all(BuiltinCommandSet())

        names = {(r.host, r.check_name) for r in results}
        self.assertIn(("be1", "Bước 3 – Docker service ngs-simo_simo-restapi"), names)
        self.assertFalse(any(host == "fe1" and "Bước 3" in name for host, name in names))
        self.assertIn(("apim1", "Bước 3 – Service wso2am"), names)
        commands_run = [c[0] for c in client.calls]
        self.assertFalse(any("systemctl status ngs-simo" in c for c in commands_run))
        self.assertIn("systemctl status wso2am", commands_run)


class TimeUtilTests(unittest.TestCase):
    def test_now_follows_configured_timezone(self):
        from datetime import datetime, timedelta, timezone
        from src import timeutil

        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "config.yaml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("general:\n  timezone: Asia/Ho_Chi_Minh\n")
            load_config(path)
            expected = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=7)
            self.assertLess(abs((timeutil.now() - expected).total_seconds()), 5)

            # Máy không có dữ liệu múi giờ: vẫn ra giờ Việt Nam nhờ offset cố định
            from zoneinfo import ZoneInfoNotFoundError
            timeutil._zone.cache_clear()
            with patch("src.timeutil.ZoneInfo", side_effect=ZoneInfoNotFoundError("no tzdata")):
                self.assertLess(abs((timeutil.now() - expected).total_seconds()), 5)
            timeutil._zone.cache_clear()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class SSHClientRunTests(unittest.TestCase):
    def _client_with_channel(self, stdout=b"ok", stderr=b"", exit_code=0):
        from unittest.mock import MagicMock
        from src.ssh_manager import SSHClient, SSHConfig

        stdin, out, err = MagicMock(), MagicMock(), MagicMock()
        out.read.return_value = stdout
        err.read.return_value = stderr
        out.channel.recv_exit_status.return_value = exit_code
        client = SSHClient(SSHConfig(host="10.0.0.1"))
        client._client = MagicMock()
        client._client.exec_command.return_value = (stdin, out, err)
        return client, stdin

    def test_run_writes_input_to_stdin_and_closes_it(self):
        client, stdin = self._client_with_channel(stdout=b"System altered.\n")
        res = client.run("sqlplus -S / as sysdba", timeout=30, input="alter system archive log current;\nEXIT;\n")
        stdin.write.assert_called_once_with("alter system archive log current;\nEXIT;\n")
        stdin.channel.shutdown_write.assert_called_once()
        self.assertEqual(res.stdout, "System altered.")
        self.assertEqual(res.command, "sqlplus -S / as sysdba")
        self.assertTrue(res.success)

    def test_run_without_input_only_closes_stdin(self):
        client, stdin = self._client_with_channel(stderr=b"boom", exit_code=2)
        res = client.run("false", timeout=5)
        stdin.write.assert_not_called()
        stdin.channel.shutdown_write.assert_called_once()
        self.assertEqual((res.exit_code, res.success, res.stderr), (2, False, "boom"))


if __name__ == "__main__":
    unittest.main()
