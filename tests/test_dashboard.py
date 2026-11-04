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
from narrow.dashboard import DashboardServer, task_detail, task_diff
from narrow.review_gate import build_review_report


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

    def request(self, method: str, path: str, *, host: str | None = None, body: bytes | None = None,
                authenticated: bool = True, token: str | None = None, origin: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {}
        if host:
            headers["Host"] = host
        if authenticated:
            headers["X-NARROW-Token"] = token or self.server.api_token
        if origin:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Length"] = str(len(body))
            headers["Content-Type"] = "application/json"
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

    def test_dashboard_rejects_mutation_without_same_origin_and_token(self):
        status, _, body = self.request("POST", "/api/v1/tasks", body=b"{}", origin=None)
        self.assertEqual(status, 403)
        self.assertIn(b"unauthorized", body.lower())

        same_origin = f"http://127.0.0.1:{self.server.server_port}"
        status, _, body = self.request(
            "POST", "/api/v1/tasks", body=b"{}", origin=same_origin
        )
        self.assertEqual(status, 404)  # Authenticated writes are routed only to review decisions.

        status, _, _ = self.request(
            "POST", "/api/v1/tasks", body=b"{}", authenticated=False, origin=same_origin
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "PUT", "/api/v1/tasks", body=b"{}", token="wrong-token", origin=same_origin
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "DELETE", "/api/v1/tasks", body=b"{}", origin=same_origin
        )
        self.assertEqual(status, 403)

    def test_dashboard_review_actions_record_changes_and_rejection(self):
        self.store.update_status(self.task["id"], "READY_FOR_REVIEW")
        origin = f"http://127.0.0.1:{self.server.server_port}"
        path = f"/api/v1/tasks/{self.task['id']}/review-decision"

        status, _, body = self.request(
            "POST", path,
            body=json.dumps({
                "decision": "request_changes",
                "reason": "Please add regression coverage for the timeout behavior.",
            }).encode("utf-8"),
            origin=origin,
        )
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["task_status"], "NEEDS_REVIEW")
        self.assertEqual(self.store.get_task(self.task["id"])["status"], "NEEDS_REVIEW")

        status, _, body = self.request(
            "POST", path,
            body=json.dumps({
                "decision": "reject",
                "reason": "The proposed approach does not meet the requirement.",
            }).encode("utf-8"),
            origin=origin,
        )
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["task_status"], "CLOSED")
        self.assertTrue(Path(self.task["worktree"]).is_dir())
        event_types = [event["type"] for event in self.store.events(self.task["id"])]
        self.assertIn("HUMAN_REVIEW_CHANGES_REQUESTED", event_types)
        self.assertIn("HUMAN_REVIEW_REJECTED", event_types)

    def test_dashboard_acceptance_action_requires_evidence_and_review_ready_status(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        path = f"/api/v1/tasks/{self.task['id']}/review-decision"
        self.store.update_status(self.task["id"], "READY_FOR_REVIEW")
        status, _, body = self.request(
            "POST", path,
            body=json.dumps({
                "decision": "accept_criterion",
                "criterion": 1,
                "evidence": "I reviewed criterion one against the current diff.",
            }).encode("utf-8"),
            origin=origin,
        )
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["decision"], "accept_criterion")
        self.assertEqual(result["report"]["acceptance"]["accepted_current"], [1])

    def test_dashboard_approve_endpoint_cannot_bypass_finish_gate(self):
        self.store.update_status(self.task["id"], "READY_FOR_REVIEW")
        origin = f"http://127.0.0.1:{self.server.server_port}"
        status, _, body = self.request(
            "POST", f"/api/v1/tasks/{self.task['id']}/review-decision",
            body=json.dumps({"decision": "approve"}).encode("utf-8"),
            origin=origin,
        )
        self.assertEqual(status, 409)
        result = json.loads(body)
        self.assertFalse(result["ok"])
        self.assertEqual(self.store.get_task(self.task["id"])["status"], "READY_FOR_REVIEW")


    def test_dashboard_api_rejects_missing_or_wrong_token(self):
        status, _, body = self.request("GET", "/api/v1/tasks", authenticated=False)
        self.assertEqual(status, 403)
        self.assertIn(b"unauthorized", body)
        status, _, _ = self.request("GET", "/api/v1/tasks", token="not-the-dashboard-token")
        self.assertEqual(status, 403)

    def test_dashboard_rejects_cross_origin_api_request(self):
        status, _, _ = self.request(
            "GET", "/api/v1/tasks", origin="https://attacker.example"
        )
        self.assertEqual(status, 403)

    def test_dashboard_html_uses_local_assets_and_no_remote_dependencies(self):
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("/assets/app.js", html)
        self.assertIn("/assets/app.css", html)
        self.assertNotIn("https://", html)
        status, _, asset_body = self.request("GET", "/assets/app.js")
        self.assertEqual(status, 200)
        script = asset_body.decode("utf-8")
        self.assertIn("narrow.dashboard.token", script)
        self.assertIn("X-NARROW-Token", script)

    def test_dashboard_review_endpoint_exposes_read_only_gate_without_auto_approval(self):
        before_status = self.store.get_task(self.task["id"])["status"]
        status, _, body = self.request("GET", f"/api/v1/tasks/{self.task['id']}/review")
        self.assertEqual(status, 200)
        report = json.loads(body)
        self.assertEqual(report["schema"], "narrow.review-report.v1")
        self.assertEqual(report["decision"], "NEEDS_VERIFICATION")
        self.assertFalse(report["automatic_approval"])
        self.assertFalse(report["state_mutated"])
        self.assertFalse(report["commands_executed"])
        self.assertEqual(self.store.get_task(self.task["id"])["status"], before_status)

    def test_dashboard_diff_endpoint_shows_tracked_and_untracked_text_changes(self):
        (Path(self.task["worktree"]) / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        new_file = Path(self.task["worktree"]) / "src" / "new.py"
        new_file.write_text("ANSWER = 42\n", encoding="utf-8")
        status, _, body = self.request("GET", f"/api/v1/tasks/{self.task['id']}/diff")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["schema"], "narrow.dashboard.diff.v1")
        self.assertIn("-VALUE = 1", data["diff"])
        self.assertIn("+VALUE = 2", data["diff"])
        self.assertIn("+ANSWER = 42", data["diff"])
        self.assertEqual(data["allowed"], ["src/app.py", "src/new.py"])
        self.assertEqual(data["outside"], [])
        self.assertEqual(data["denied"], [])

    def test_dashboard_diff_rejects_unsafe_workspace_links(self):
        target = Path(self.task["worktree"]) / "src" / "redirect.py"
        target.symlink_to(self.root / "src" / "app.py")
        data = task_diff(self.store, self.task["id"])
        self.assertTrue(data["unsafe_links"])
        self.assertEqual(data["diff"], "")

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
