from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from narrow.core import NarrowError, Store, create_task, init_project
from narrow.policy_manifest import read_policy_manifest, write_policy_manifest


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


class PolicyManifestTests(unittest.TestCase):
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
        init_project(self.root)
        self.store = Store(self.root)
        self.task = create_task(
            self.store,
            "Improve the sample",
            "Update src/app.py while preserving the auth boundary.",
            ["src/**"],
            ["src/auth/**"],
            ["The app behavior is covered by a passing test."],
        )
        self.execution_id = str(uuid.uuid4())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manifest_is_task_scoped_and_contains_no_host_state_paths(self):
        result = write_policy_manifest(self.store, self.task["id"], self.execution_id)
        manifest = read_policy_manifest(
            result.path,
            expected_task_id=self.task["id"],
            expected_execution_id=self.execution_id,
        )
        self.assertEqual(manifest["schema"], "narrow.agent-policy.v1")
        self.assertEqual(manifest["workspace_root"], "/workspace")
        self.assertEqual(manifest["scope"]["allowed_paths"], ["src/**"])
        self.assertEqual(manifest["scope"]["denied_paths"], ["src/auth/**"])
        self.assertEqual(manifest["acceptance_criteria"][0]["text"], "The app behavior is covered by a passing test.")
        self.assertNotIn(str(self.root), result.path.read_text(encoding="utf-8"))
        self.assertNotIn("state.sqlite3", result.path.read_text(encoding="utf-8"))
        self.assertFalse(result.path.is_relative_to(Path(self.task["worktree"])))
        self.assertTrue(result.path.stat().st_mode & 0o400)
        if os.name != "nt":
            self.assertEqual(result.path.stat().st_mode & 0o777, 0o400)

    def test_manifest_rejects_tampered_payload(self):
        result = write_policy_manifest(self.store, self.task["id"], self.execution_id)
        envelope = json.loads(result.path.read_text(encoding="utf-8"))
        envelope["manifest"]["scope"]["allowed_paths"] = ["**"]
        result.path.chmod(0o600)
        result.path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaisesRegex(NarrowError, "checksum mismatch"):
            read_policy_manifest(result.path)

    def test_manifest_is_bound_to_the_requested_task_and_execution(self):
        result = write_policy_manifest(self.store, self.task["id"], self.execution_id)
        with self.assertRaisesRegex(NarrowError, "different task"):
            read_policy_manifest(result.path, expected_task_id="another-task")
        with self.assertRaisesRegex(NarrowError, "different execution"):
            read_policy_manifest(result.path, expected_execution_id=str(uuid.uuid4()))

    def test_writer_refuses_to_overwrite_an_existing_execution_manifest(self):
        first = write_policy_manifest(self.store, self.task["id"], self.execution_id)
        with self.assertRaisesRegex(NarrowError, "already exists"):
            write_policy_manifest(self.store, self.task["id"], self.execution_id)
        self.assertTrue(first.path.exists())

    def test_writer_rejects_parent_traversal_in_stored_scope(self):
        with self.store.connect() as db:
            db.execute(
                "UPDATE tasks SET allowed_json=? WHERE id=?",
                (json.dumps(["../outside"]), self.task["id"]),
            )
        with self.assertRaisesRegex(NarrowError, "parent traversal"):
            write_policy_manifest(self.store, self.task["id"], str(uuid.uuid4()))

    def test_reader_rejects_symlinked_manifest(self):
        result = write_policy_manifest(self.store, self.task["id"], self.execution_id)
        link = result.path.with_name("linked-policy.json")
        try:
            link.symlink_to(result.path)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable on this platform.")
        with self.assertRaisesRegex(NarrowError, "regular, unlinked"):
            read_policy_manifest(link)


if __name__ == "__main__":
    unittest.main()
