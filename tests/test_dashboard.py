from __future__ import annotations

import http.client
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.core import Store, create_task, init_project, record_note
from narrow.dashboard import DashboardServer, task_detail


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def make_repo(parent: Path) -> Path:
    root = parent / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    git(root, "config", "user.name", "NARROW tests")
    git(root, "config", "user.email", "narrow@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.temp_path / "cache")})
        self.env.start()
        self.root = make_repo(self.temp_path)
        init_project(self.root)
        self.store = Store(self.root)
        self.task = create_task(
            self.store,
            "Improve dashboard",
            "Show a safe task overview.",
            ["src/**"],
            ["src/secrets/**"],
            ["The test suite passes."],
        )
        self.store.add_event(self.task["id"], "AGENT_RUN_STARTED", {"provider": "claude"})
        record_note(self.store, self.task["id"], "NOTE", "private marker: do-not-expose-12345")
        self.server = DashboardServer(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.env.stop()
        self.temp.cleanup()

    def request(self, method: str, path: str, *, host: str | None = None, body: bytes | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {}
        if host:
            headers["Host"] = host
        if body is not None:
            headers["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = (response.status, response.getheaders(), response.read())
        connection.close()
        return result

    def test_dashboard_api_returns_sanitized_task_and_activity(self):
        status, headers, body = self.request("GET", "/api/v1/tasks")
        self.assertEqual(status, 200)
        self.assertIn(("X-Content-Type-Options", "nosniff"), headers)
        tasks = json.loads(body)
        self.assertEqual(tasks[0]["id"], self.task["id"])
        self.assertEqual(tasks[0]["title"], "Improve dashboard")

        status, _, body = self.request("GET", f"/api/v1/tasks/{self.task['id']}")
        self.assertEqual(status, 200)
        detail = json.loads(body)
        self.assertEqual(detail["schema"], "narrow.dashboard.task.v1")
        self.assertTrue(detail["activity"])
        self.assertNotIn("do-not-expose-12345", body.decode("utf-8"))

    def test_dashboard_overview_counts_tasks(self):
        status, _, body = self.request("GET", "/api/v1/overview")
        self.assertEqual(status, 200)
        overview = json.loads(body)
        self.assertEqual(overview["counts"]["active"], 1)
        self.assertEqual(overview["counts"]["verified_complete"], 0)

    def test_dashboard_rejects_untrusted_host_header(self):
        status, _, body = self.request("GET", "/", host="attacker.example")
        self.assertEqual(status, 403)
        self.assertIn(b"Localhost access only", body)

    def test_dashboard_is_read_only(self):
        status, _, _ = self.request("POST", "/api/v1/tasks", body=b"{}")
        self.assertEqual(status, 405)
        self.assertEqual(self.store.get_task(self.task["id"])["status"], "ACTIVE")

    def test_dashboard_html_uses_local_assets_and_no_remote_dependencies(self):
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("/assets/app.js", html)
        self.assertIn("/assets/app.css", html)
        self.assertNotIn("https://", html)

    def test_task_detail_never_returns_check_output_or_note_payloads(self):
        detail = task_detail(self.store, self.task["id"])
        serialized = json.dumps(detail)
        self.assertNotIn("private marker", serialized)
        self.assertNotIn("payload", serialized)
        self.assertNotIn("output_tail", serialized)


    def test_dashboard_counts_ready_for_review_using_the_persisted_task_status(self):
        self.store.update_status(self.task["id"], "READY_FOR_REVIEW")
        status, _, body = self.request("GET", "/api/v1/overview")
        self.assertEqual(status, 200)
        overview = json.loads(body)
        self.assertEqual(overview["counts"]["ready_for_review"], 1)


if __name__ == "__main__":
    unittest.main()
