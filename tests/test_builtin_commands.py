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
}


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
        self.assertEqual(main["default"]["command"], "crs_stat -t")

        resolved = self.store.load_command_set().resolve("oracle.crs_status", "main")
        self.assertEqual(resolved["command"], "/opt/grid/bin/crs_stat -t")

        step = self.store.reset("oracle.crs_status")
        self.assertFalse(step["is_modified"])
        self.assertEqual(step["commands"][0]["command"], "crs_stat -t")
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
        commands.run(client, "oracle.asm_diskgroup", "main", {**ORACLE_CTX, "asm_instance": "+ASM2"})
        command, timeout, stdin = client.calls[0]
        self.assertEqual(command, "export ORACLE_SID=+ASM2; sqlplus -S / as sysasm")
        self.assertEqual(timeout, 60)
        self.assertIn("from v$asm_diskgroup;", stdin)
        self.assertTrue(stdin.endswith("EXIT;\n"))

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
        # Không lấy được background_dump_dest → dùng đường dẫn mặc định
        self.assertEqual(
            client.calls[1][0],
            "tail -1000 /u01/app/oracle/diag/rdbms/simo1/simo1/trace/alert_simo1.log",
        )

    def test_crs_fallback_runs_when_command_not_found(self):
        healthy = "Name           Type           Target    State     Host\n------\nora.asm        ora.asm.type   ONLINE    ONLINE    db1\n"

        def responder(command, stdin):
            if command == "crs_stat -t":
                return ("", "bash: crs_stat: command not found", 127)
            return (healthy, "", 0)

        client = FakeClient(responder)
        result = oracle_checks.check_crs_status(client, "db1", BuiltinCommandSet(), ORACLE_CTX)
        self.assertEqual(result.status, "OK")
        self.assertEqual(client.calls[1][0], "/u01/app/19.3.0/grid/bin/crs_stat -t")

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
