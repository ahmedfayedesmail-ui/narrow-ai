from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.core import Store, accept_criterion, create_task, init_project, save_check_run, snapshot_hash
from narrow.review_gate import build_review_report, evaluate_repair_continuation


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=True)
    return result.stdout.strip()


def make_repo(parent: Path) -> Path:
    root = parent / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    git(root, "config", "user.name", "NARROW Review Tests")
    git(root, "config", "user.email", "review@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text(".env\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.base / "cache")})
        self.env.start()
        self.root = make_repo(self.base)
        init_project(self.root)
        self.store = Store(self.root)
        self.task = create_task(
            self.store, "Review task", "Review evidence without approving automatically.",
            ["src/**", "tests/**"], ["src/auth/**"],
            ["Behavior remains correct.", "No secrets are exposed."],
        )
        self.workspace = Path(self.task["worktree"])

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def _configure_and_pass(self):
        path = self.root / ".narrow" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["checks"] = {"unit": {"command": ["python", "-m", "unittest", "discover", "-s", "tests"], "timeout_seconds": 120}}
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        save_check_run(
            self.store, self.task["id"], "unit",
            config["checks"]["unit"]["command"], snapshot_hash(self.workspace),
            0, 15, "tests passed",
        )
        self.store.update_status(self.task["id"], "READY_FOR_REVIEW")

    def test_review_is_read_only_and_requires_explicit_human_acceptance(self):
        before_events = len(self.store.events(self.task["id"]))
        report = build_review_report(self.store, self.task["id"])
        self.assertEqual(report["decision"], "NEEDS_VERIFICATION")
        self.assertFalse(report["automatic_approval"])
        self.assertFalse(report["state_mutated"])
        self.assertFalse(report["commands_executed"])
        self.assertEqual(self.store.get_task(self.task["id"])["status"], "ACTIVE")
        self.assertEqual(len(self.store.events(self.task["id"])), before_events)

    def test_valid_machine_evidence_still_waits_for_human_acceptance(self):
        self._configure_and_pass()
        report = build_review_report(self.store, self.task["id"])
        self.assertEqual(report["decision"], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(report["verification"]["passing_current"], 1)
        self.assertEqual(report["acceptance"]["pending_or_stale"], [1, 2])

    def test_ready_report_still_does_not_complete_task_automatically(self):
        self._configure_and_pass()
        for index in (1, 2):
            accept_criterion(self.store, self.task["id"], index, "Reviewed the current diff and verified the criterion manually.")
        report = build_review_report(self.store, self.task["id"])
        self.assertEqual(report["decision"], "READY_FOR_FINISH_GATE")
        self.assertFalse(report["automatic_approval"])
        self.assertEqual(self.store.get_task(self.task["id"])["status"], "READY_FOR_REVIEW")

    def test_out_of_scope_and_denied_paths_are_blockers(self):
        (self.workspace / "docs").mkdir()
        (self.workspace / "docs" / "notes.md").write_text("outside\n", encoding="utf-8")
        (self.workspace / "src" / "auth").mkdir()
        (self.workspace / "src" / "auth" / "provider.py").write_text("token = None\n", encoding="utf-8")
        report = build_review_report(self.store, self.task["id"])
        self.assertEqual(report["decision"], "BLOCKED")
        scope_gate = next(item for item in report["gates"] if item["name"] == "scope")
        self.assertEqual(scope_gate["status"], "BLOCKED")

    def test_credential_like_paths_are_blocked_without_exposing_file_contents(self):
        secrets = self.workspace / "src" / "credentials"
        secrets.mkdir()
        (secrets / "provider.pem").write_text("DO_NOT_READ_THIS_TEST_SECRET", encoding="utf-8")
        report = build_review_report(self.store, self.task["id"])
        self.assertEqual(report["decision"], "BLOCKED")
        self.assertNotIn("DO_NOT_READ_THIS_TEST_SECRET", json.dumps(report))
        self.assertTrue(any(item["code"] == "SENSITIVE_FILE_PATH" for item in report["blockers"]))

    def test_repair_continuation_allows_only_plain_verification_failure(self):
        report = {
            "schema": "narrow.review-report.v1",
            "decision": "NEEDS_VERIFICATION",
            "automatic_approval": False,
            "state_mutated": False,
            "commands_executed": False,
            "blockers": [],
            "risk_flags": [],
        }
        self.assertEqual(evaluate_repair_continuation(report)["decision"], "CONTINUE")

    def test_repair_continuation_stops_on_safety_blockers(self):
        report = {
            "schema": "narrow.review-report.v1",
            "decision": "BLOCKED",
            "automatic_approval": False,
            "state_mutated": False,
            "commands_executed": False,
            "blockers": [{"code": "SCOPE"}],
            "risk_flags": [],
        }
        self.assertEqual(evaluate_repair_continuation(report)["decision"], "STOP")

    def test_repair_continuation_escalates_security_sensitive_paths(self):
        report = {
            "schema": "narrow.review-report.v1",
            "decision": "NEEDS_VERIFICATION",
            "automatic_approval": False,
            "state_mutated": False,
            "commands_executed": False,
            "blockers": [],
            "risk_flags": [{"code": "SECURITY_SENSITIVE_AREA"}],
        }
        self.assertEqual(evaluate_repair_continuation(report)["decision"], "ESCALATE")

    def test_repair_continuation_fails_closed_on_malformed_review_packet(self):
        report = {
            "schema": "narrow.review-report.v1",
            "decision": "NEEDS_VERIFICATION",
            "automatic_approval": True,
            "state_mutated": False,
            "commands_executed": False,
            "blockers": [],
            "risk_flags": [],
        }
        self.assertEqual(evaluate_repair_continuation(report)["decision"], "STOP")

    def test_security_sensitive_paths_are_flagged_for_human_scrutiny(self):
        workflows = self.workspace / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "release.yml").write_text("name: release\n", encoding="utf-8")
        report = build_review_report(self.store, self.task["id"])
        self.assertTrue(any(item["code"] == "SECURITY_SENSITIVE_AREA" for item in report["risk_flags"]))
        self.assertEqual(report["decision"], "BLOCKED")  # It is outside the task's explicit allowlist.

    def test_stale_passing_check_is_not_counted_as_current_evidence(self):
        self._configure_and_pass()
        (self.workspace / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = build_review_report(self.store, self.task["id"])
        self.assertEqual(report["decision"], "NEEDS_VERIFICATION")
        self.assertEqual(report["verification"]["items"][0]["status"], "STALE")


if __name__ == "__main__":
    unittest.main()
