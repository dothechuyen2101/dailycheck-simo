import os
import tempfile
import unittest
from unittest.mock import patch

from src.config_manager import load_config
from src.dynamic_modules import _result_status, validate_module_payload, build_module_from_template, DynamicModule, DynamicModuleHost, DynamicModuleStep, run_module_checks
from src.security import decrypt_secret


class DynamicModuleTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        self.config_path = path
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "general:\n"
                "  timezone: Asia/Ho_Chi_Minh\n"
                "  command_timeout: 60\n"
                "security:\n"
                "  encryption_key: test-secret\n"
            )
        load_config(self.config_path)

    def tearDown(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def test_validate_encrypts_password_and_keeps_rules(self):
        payload = {
            "name": "API Nodes",
            "system_name": "API",
            "enabled": True,
            "timezone": "Asia/Ho_Chi_Minh",
            "daily_time": "08:00",
            "recipient_emails": ["ops@example.com"],
            "hosts": [
                {
                    "name": "api-1",
                    "ip": "10.0.0.1",
                    "port": 22,
                    "username": "root",
                    "auth_type": "password",
                    "password": "secret123",
                }
            ],
            "steps": [
                {
                    "name": "Health",
                    "command": "echo ok",
                    "timeout_sec": 10,
                    "run_on_each_host": True,
                    "rules": {
                        "nonzero_exit_status": "CRITICAL",
                        "warning_keywords": ["warn"],
                        "critical_keywords": ["fatal"],
                    },
                }
            ],
        }
        normalized = validate_module_payload(payload)
        self.assertNotEqual(normalized["hosts"][0]["encrypted_password"], "secret123")
        self.assertEqual(decrypt_secret(normalized["hosts"][0]["encrypted_password"]), "secret123")
        self.assertEqual(normalized["steps"][0]["rules"]["nonzero_exit_status"], "CRITICAL")

    def test_validate_requires_recipients(self):
        payload = {
            "name": "API Nodes",
            "system_name": "API",
            "enabled": True,
            "timezone": "Asia/Ho_Chi_Minh",
            "daily_time": "08:00",
            "recipient_emails": [],
            "hosts": [
                {
                    "name": "api-1",
                    "ip": "10.0.0.1",
                    "port": 22,
                    "username": "root",
                    "auth_type": "key",
                    "key_path": "~/.ssh/id_rsa",
                }
            ],
            "steps": [
                {
                    "name": "Health",
                    "command": "echo ok",
                    "timeout_sec": 10,
                    "run_on_each_host": True,
                    "rules": {},
                }
            ],
        }
        with self.assertRaises(ValueError):
            validate_module_payload(payload)

    def test_result_status_priority(self):
        rules = {
            "nonzero_exit_status": "WARNING",
            "warning_keywords": ["warn"],
            "critical_keywords": ["fatal"],
        }
        self.assertEqual(_result_status(True, "all good", rules), "OK")
        self.assertEqual(_result_status(False, "command failed", rules), "WARNING")
        self.assertEqual(_result_status(True, "warn: high load", rules), "WARNING")
        self.assertEqual(_result_status(True, "warn and fatal", rules), "CRITICAL")

    def test_build_template_contains_steps_and_host_variables(self):
        payload = build_module_from_template("oracle")
        self.assertEqual(payload["template_key"], "oracle")
        self.assertEqual(payload["system_name"], "Oracle")
        self.assertTrue(payload["steps"])
        self.assertIn("instance_name", payload["hosts"][0]["variables"])

    def test_validate_preserves_host_variables(self):
        payload = {
            "name": "Mongo Template",
            "system_name": "MongoDB",
            "template_key": "mongodb",
            "enabled": True,
            "timezone": "Asia/Ho_Chi_Minh",
            "daily_time": "08:00",
            "recipient_emails": ["ops@example.com"],
            "hosts": [
                {
                    "name": "mongo-1",
                    "ip": "10.0.0.2",
                    "port": 22,
                    "username": "root",
                    "auth_type": "password",
                    "password": "secret123",
                    "variables": {"service_name": "mongod"},
                }
            ],
            "steps": [
                {
                    "name": "Service",
                    "command": "systemctl status {{service_name}}",
                    "timeout_sec": 10,
                    "run_on_each_host": True,
                    "rules": {},
                }
            ],
        }
        normalized = validate_module_payload(payload)
        self.assertEqual(normalized["template_key"], "mongodb")
        self.assertEqual(normalized["hosts"][0]["variables"]["service_name"], "mongod")

    def test_run_module_checks_does_not_reconnect_failed_host_per_step(self):
        module = DynamicModule(
            id=1,
            name="Retry Test",
            system_name="MongoDB",
            enabled=True,
            timezone="Asia/Ho_Chi_Minh",
            daily_time="08:00",
            recipient_emails=["ops@example.com"],
            hosts=[
                DynamicModuleHost(
                    id=10,
                    name="host-a",
                    ip="10.0.0.1",
                    username="root",
                )
            ],
            steps=[
                DynamicModuleStep(id=1, name="step1", command="echo 1"),
                DynamicModuleStep(id=2, name="step2", command="echo 2"),
                DynamicModuleStep(id=3, name="step3", command="echo 3"),
            ],
        )

        with patch("src.dynamic_modules._ssh_client_for_host", side_effect=ConnectionError("Cannot SSH to 10.0.0.1")) as mocked_connect:
            results = run_module_checks(module)

        self.assertEqual(mocked_connect.call_count, 1)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(result.status == "ERROR" for result in results))


if __name__ == "__main__":
    unittest.main()
