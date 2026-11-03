from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from narrow.core import NarrowError
from narrow.locks import task_execution_lock


class TaskExecutionLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name) / ".narrow"
        self.state_dir.mkdir()
        self.task_id = "task_0123456789"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _child_attempt(self) -> subprocess.CompletedProcess[str]:
        script = (
            "import sys\nfrom pathlib import Path\n"
            "from narrow.locks import task_execution_lock\n"
            "from narrow.core import NarrowError\n"
            "try:\n"
            " with task_execution_lock(Path(sys.argv[1]), sys.argv[2]): print('ACQUIRED')\n"
            "except NarrowError as exc:\n"
            " print('BUSY' if 'already has an active' in str(exc) else 'ERROR')\n"
            " sys.exit(17)\n"
        )
        env = dict(os.environ)
        source_root = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run([sys.executable, "-c", script, str(self.state_dir), self.task_id],
            capture_output=True, text=True, env=env, timeout=10, check=False)

    def test_same_task_is_single_flight_across_processes(self) -> None:
        with task_execution_lock(self.state_dir, self.task_id):
            result = self._child_attempt()
            self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
            self.assertIn("BUSY", result.stdout)
        result = self._child_attempt()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ACQUIRED", result.stdout)

    def test_different_tasks_can_run_concurrently(self) -> None:
        with task_execution_lock(self.state_dir, self.task_id):
            with task_execution_lock(self.state_dir, "task_another_123"):
                pass

    @unittest.skipIf(os.name == "nt", "POSIX inherited flock descriptors are tested on Linux/macOS.")
    def test_inherited_descriptor_keeps_lease_after_parent_context_closes(self) -> None:
        with task_execution_lock(self.state_dir, self.task_id) as lock_fd:
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.0)"],
                pass_fds=(lock_fd,), close_fds=True)
        try:
            with self.assertRaises(NarrowError):
                with task_execution_lock(self.state_dir, self.task_id):
                    self.fail("A second supervisor acquired a lease still held by the child.")
        finally:
            child.wait(timeout=5)
        with task_execution_lock(self.state_dir, self.task_id):
            pass

    def test_invalid_task_identifier_is_rejected(self) -> None:
        with self.assertRaises(NarrowError):
            with task_execution_lock(self.state_dir, "../escape"):
                pass

    def test_symlinked_lock_directory_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        try:
            (self.state_dir / "locks").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symbolic links requires platform privileges.")
        with self.assertRaises(NarrowError):
            with task_execution_lock(self.state_dir, self.task_id):
                pass

    def test_hard_linked_lock_file_is_rejected(self) -> None:
        lock_dir = self.state_dir / "locks"
        lock_dir.mkdir()
        source = Path(self.temp.name) / "linked-file"
        source.write_bytes(b"x")
        try:
            os.link(source, lock_dir / f"{self.task_id}.lock")
        except OSError:
            self.skipTest("Hard links are unavailable on this filesystem.")
        with self.assertRaises(NarrowError):
            with task_execution_lock(self.state_dir, self.task_id):
                pass


if __name__ == "__main__":
    unittest.main()
