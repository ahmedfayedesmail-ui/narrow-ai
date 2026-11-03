from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from narrow.windows_job import spawn_process_with_job


@skipUnless(os.name == "nt", "Windows Job Objects are available only on Windows.")
class WindowsJobTests(unittest.TestCase):
    def test_child_is_not_resumed_until_after_job_assignment(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "child-started.txt"
            script = (
                "from pathlib import Path; import time; "
                f"Path({str(marker)!r}).write_text('started', encoding='utf-8'); "
                "time.sleep(0.2)"
            )
            from narrow import windows_job

            real_resume = windows_job._resume_primary_thread
            real_assign = windows_job.WindowsJob.assign_suspended
            observed: list[int] = []
            assigned: list[int] = []

            def verify_assignment(job_object, process) -> None:
                real_assign(job_object, process)
                assigned.append(process.pid)

            def verify_suspended(pid: int) -> None:
                self.assertEqual(assigned, [pid], "The primary thread was resumed before Job assignment.")
                self.assertFalse(marker.exists(), "Child code ran before the resume step.")
                real_resume(pid)
                observed.append(pid)

            with patch.object(windows_job.WindowsJob, "assign_suspended", verify_assignment), \
                 patch("narrow.windows_job._resume_primary_thread", side_effect=verify_suspended):
                process, job = spawn_process_with_job(
                    [sys.executable, "-c", script],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            try:
                self.assertIsNotNone(job)
                self.assertEqual(observed, [process.pid])
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual(marker.read_text(encoding="utf-8"), "started")
            finally:
                if job is not None:
                    job.close()

    def test_closing_job_terminates_child_and_grandchild(self):
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / "grandchild.pid"
            grandchild_script = "import time; time.sleep(60)"
            parent_script = (
                "import subprocess, sys, time; from pathlib import Path; "
                f"p=subprocess.Popen([sys.executable, '-c', {grandchild_script!r}]); "
                f"Path({str(pid_file)!r}).write_text(str(p.pid), encoding='ascii'); "
                "time.sleep(60)"
            )
            parent, job = spawn_process_with_job(
                [sys.executable, "-c", parent_script],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5
                while not pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(pid_file.exists(), "The child did not report its grandchild PID.")
                grandchild_pid = int(pid_file.read_text(encoding="ascii"))
                self.assertIsNotNone(job)
                job.close()
                self.assertIsNotNone(parent.wait(timeout=5))
                self.assertTrue(_windows_process_exited(grandchild_pid, timeout_ms=5000))
            finally:
                if job is not None:
                    job.close()
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)



class NonWindowsJobCompatibilityTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "This test covers the non-Windows Popen fallback.")
    def test_spawn_helper_preserves_standard_popen_behavior_off_windows(self):
        process, job = spawn_process_with_job(
            [sys.executable, "-c", "print('narrow-child-ok')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(timeout=5)
        self.assertIsNone(job)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("narrow-child-ok", stdout)


def _windows_process_exited(pid: int, timeout_ms: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    synchronize = 0x00100000
    wait_object_0 = 0
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER means the process no longer exists.
        return ctypes.get_last_error() == 87
    try:
        return kernel32.WaitForSingleObject(handle, timeout_ms) == wait_object_0
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
