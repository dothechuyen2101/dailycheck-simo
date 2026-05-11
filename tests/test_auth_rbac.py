import importlib
import os
import shutil
import tempfile
import unittest

from src.config_manager import load_config
from src.dynamic_modules import DynamicModuleStore
from src.logger import ResultStore, RunSummary
import src.auth as auth


class AuthRbacTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="dailycheck-auth-")
        self.data_dir = os.path.join(self.temp_dir, "data")
        self.config_path = os.path.join(self.temp_dir, "config.yaml")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(
                "general:\n"
                f"  data_dir: {self.data_dir}\n"
                f"  db_path: {self.data_dir}/dailycheck.db\n"
                "  timezone: Asia/Ho_Chi_Minh\n"
                "  command_timeout: 60\n"
                "security:\n"
                "  encryption_key: test-secret\n"
                "dashboard:\n"
                "  secret_key: test-secret-key\n"
                "auth:\n"
                "  admin_username: admin\n"
                "  admin_password: admin123\n"
            )

        load_config(self.config_path)

        import dashboard.app as app_module

        self.app_module = importlib.reload(app_module)
        self.app = self.app_module.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        self.module_store = DynamicModuleStore()
        self.result_store = ResultStore()

        auth.create_user("alice", "alicepw", role="user", display_name="Alice")
        auth.create_user("bob", "bobpw", role="user", display_name="Bob")

        self.alice_module = self.module_store.create_module(self._module_payload("Alice Module"), created_by="alice")
        self.bob_module = self.module_store.create_module(self._module_payload("Bob Module"), created_by="bob")

        self.builtin_run_id = "builtin-run"
        self.alice_run_id = "alice-run"
        self.bob_run_id = "bob-run"
        self.result_store.save_run(RunSummary(run_id=self.builtin_run_id, started_at="2026-05-11T08:00:00", run_type="builtin"))
        self.result_store.save_run(RunSummary(run_id=self.alice_run_id, started_at="2026-05-11T08:10:00", run_type="dynamic_module", module_id=self.alice_module.id, module_name=self.alice_module.name))
        self.result_store.save_run(RunSummary(run_id=self.bob_run_id, started_at="2026-05-11T08:20:00", run_type="dynamic_module", module_id=self.bob_module.id, module_name=self.bob_module.name))

        reports_dir = os.path.join(self.data_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        with open(os.path.join(reports_dir, f"report_{self.alice_run_id}.html"), "w", encoding="utf-8") as handle:
            handle.write("<html><body>Alice report</body></html>")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _module_payload(self, name: str) -> dict:
        return {
            "name": name,
            "system_name": "Custom",
            "template_key": "custom",
            "enabled": True,
            "timezone": "Asia/Ho_Chi_Minh",
            "daily_time": "08:00",
            "recipient_emails": ["ops@example.com"],
            "hosts": [
                {
                    "name": "host-1",
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
                    "rules": {},
                }
            ],
        }

    def _login(self, username: str, password: str):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )

    def test_admin_redirects_to_dashboard_after_login(self):
        response = self._login("admin", "admin123")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_user_redirects_to_modules_after_login(self):
        response = self._login("alice", "alicepw")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modules")

    def test_user_cannot_access_builtin_dashboard_or_trigger(self):
        self._login("alice", "alicepw")

        dashboard_response = self.client.get("/")
        trigger_response = self.client.post("/api/trigger")

        self.assertEqual(dashboard_response.status_code, 403)
        self.assertEqual(trigger_response.status_code, 403)

    def test_user_only_sees_owned_modules(self):
        self._login("alice", "alicepw")

        response = self.client.get("/api/modules")
        self.assertEqual(response.status_code, 200)
        modules = response.get_json()

        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0]["name"], "Alice Module")
        self.assertEqual(modules[0]["created_by"], "alice")

    def test_user_can_view_only_own_dynamic_run_and_report(self):
        self._login("alice", "alicepw")

        own_run = self.client.get(f"/run/{self.alice_run_id}")
        own_report = self.client.get(f"/report/{self.alice_run_id}")
        other_run = self.client.get(f"/run/{self.bob_run_id}")
        builtin_run = self.client.get(f"/run/{self.builtin_run_id}")

        self.assertEqual(own_run.status_code, 200)
        self.assertEqual(own_report.status_code, 200)
        self.assertEqual(other_run.status_code, 403)
        self.assertEqual(builtin_run.status_code, 403)

    def test_disabled_user_session_is_revoked(self):
        self._login("alice", "alicepw")
        alice = auth.get_user_by_username("alice")
        auth.update_user(alice.id, enabled=False)

        response = self.client.get("/modules", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
