from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from narrow.core import Store, create_task, init_project
from narrow.container_hooks import handle_container_claude_hook, import_container_hook_events
from narrow.hooks import run_hook_cli
from narrow.policy_manifest import write_policy_manifest


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def hook_input(value: dict) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(json.dumps(value).encode("utf-8")), encoding="utf-8")


class ContainerHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(self.root)], check=True, capture_output=True)
        git(self.root, "config", "user.name", "NARROW tests")
        git(self.root, "config", "user.email", "narrow@example.invalid")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "initial")
        self.cache = Path(self.temp.name) / "cache"
        self.cache.mkdir()
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        self.env.start()
        init_project(self.root)
        self.store = Store(self.root)
        self.task = create_task(
            self.store,
            "Update sample behavior",
            "Change src/app.py and preserve the authentication boundary.",
            ["src/**"],
            ["src/auth/**"],
            ["The behavior is covered by a passing test."],
        )
        self.execution_id = str(uuid.uuid4())
        self.manifest = write_policy_manifest(self.store, self.task["id"], self.execution_id)
        self.workspace = Path(self.temp.name) / "workspace"
        (self.workspace / "src").mkdir(parents=True)
        (self.workspace / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.workspace / "src" / "auth").mkdir()
        self.home = Path(self.temp.name) / "agent-home"
        self.home.mkdir()
        self.spool = self.home / ".local" / "state" / "narrow" / "hook-events.jsonl"
        self.environ = patch.dict(os.environ, {
            "HOME": str(self.home),
            "NARROW_POLICY_MANIFEST": str(self.manifest.path),
            "NARROW_TASK_ID": self.task["id"],
            "NARROW_EXECUTION_ID": self.execution_id,
            "NARROW_CONTAINER_EVENT_SPOOL": str(self.spool),
        })
        self.environ.start()

    def tearDown(self) -> None:
        self.environ.stop()
        self.env.stop()
        self.temp.cleanup()

    def invoke(self, event: str, payload: dict) -> int:
        with patch("narrow.container_hooks.sys.stdin", hook_input(payload)):
            return handle_container_claude_hook(event, workspace_override=self.workspace)

    def read_events(self) -> list[dict]:
        if not self.spool.exists():
            return []
        return [json.loads(line) for line in self.spool.read_text(encoding="utf-8").splitlines()]

    def test_session_start_uses_policy_without_opening_host_store(self):
        with patch.object(Store, "connect", side_effect=AssertionError("container hook opened SQLite")):
            with patch("narrow.container_hooks.sys.stdin", hook_input({"session_id": "session-1"})):
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    result = handle_container_claude_hook("session-start", workspace_override=self.workspace)
        self.assertEqual(result, 0)
        context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Workspace: /workspace", context)
        self.assertIn("src/**", context)
        self.assertNotIn(str(self.root), context)
        self.assertEqual(self.read_events()[0]["event_type"], "CLAUDE_SESSION_START")

    def test_hook_cli_routes_to_manifest_mode_without_project_root(self):
        with patch.dict(os.environ, {"NARROW_PROJECT_ROOT": ""}):
            with patch("narrow.container_hooks.sys.stdin", hook_input({"session_id": "cli-session"})):
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    result = run_hook_cli(["claude", "session-start"])
        self.assertEqual(result, 0)
        context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("NARROW CONTAINER TASK POLICY", context)
        self.assertEqual(self.read_events()[-1]["event_type"], "CLAUDE_SESSION_START")

    def test_pre_tool_use_allows_only_in_scope_direct_writes(self):
        result = self.invoke("pre-tool-use", {
            "session_id": "s1",
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.workspace / "src" / "app.py")},
        })
        self.assertEqual(result, 0)
        events = self.read_events()
        self.assertEqual(events[-1]["event_type"], "CLAUDE_FILE_WRITE_ALLOWED")
        self.assertEqual(events[-1]["payload"]["target"], "src/app.py")

    def test_pre_tool_use_denies_explicitly_denied_and_traversal_paths(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            denied = self.invoke("pre-tool-use", {
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.workspace / "src" / "auth" / "secret.py")},
            })
            traversal = self.invoke("pre-tool-use", {
                "tool_name": "Edit",
                "tool_input": {"file_path": "../outside.txt"},
            })
        self.assertEqual(denied, 2)
        self.assertEqual(traversal, 2)
        self.assertFalse((self.workspace.parent / "outside.txt").exists())

    def test_pre_tool_use_fails_closed_when_write_event_spool_is_unavailable(self):
        with patch.dict(os.environ, {"NARROW_CONTAINER_EVENT_SPOOL": ""}):
            with patch("sys.stderr", new_callable=io.StringIO):
                result = self.invoke("pre-tool-use", {
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(self.workspace / "src" / "app.py")},
                })
        self.assertEqual(result, 2)

    def test_host_importer_marks_agent_spool_records_as_untrusted_telemetry(self):
        self.invoke("session-start", {"session_id": "spool-import"})
        imported = import_container_hook_events(
            self.store, self.task["id"], self.execution_id, self.spool
        )
        self.assertEqual(imported, 1)
        replayed = import_container_hook_events(
            self.store, self.task["id"], self.execution_id, self.spool
        )
        self.assertEqual(replayed, 0)
        events = self.store.events(self.task["id"], limit=100)
        telemetry = [event for event in events if event["type"] == "CONTAINER_HOOK_TELEMETRY"]
        self.assertEqual(len(telemetry), 1)
        self.assertEqual(telemetry[0]["payload"]["trust"], "untrusted_agent_telemetry")
        self.assertEqual(telemetry[0]["payload"]["reported_event_type"], "CLAUDE_SESSION_START")

    def test_host_importer_ignores_spool_records_from_another_execution(self):
        self.invoke("session-start", {"session_id": "spool-import"})
        with self.spool.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "schema": "narrow.container-hook-event.v1",
                "task_id": self.task["id"],
                "execution_id": str(uuid.uuid4()),
                "event_type": "CLAUDE_SESSION_START",
                "created_at": "2026-11-03T00:00:00+00:00",
                "payload": {},
            }) + "\n")
        imported = import_container_hook_events(
            self.store, self.task["id"], self.execution_id, self.spool
        )
        self.assertEqual(imported, 1)

    def test_pre_compact_writes_a_task_scoped_handoff_inside_workspace(self):
        result = self.invoke("pre-compact", {"session_id": "s1"})
        self.assertEqual(result, 0)
        handoff = (self.workspace / "NARROW_HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn(self.task["id"], handoff)
        self.assertIn("preserve the authentication boundary", handoff)
        self.assertNotIn(str(self.root), handoff)
        self.assertEqual(self.read_events()[-1]["event_type"], "CLAUDE_PRECOMPACT_HANDOFF")


if __name__ == "__main__":
    unittest.main()
