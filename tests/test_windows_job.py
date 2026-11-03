from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import skipUnless

from narrow.windows_job import spawn_process_with_job


@skipUnless(os.name == "nt", "Windows Job Objects are available only on Windows.")
class WindowsJobTests(unittest.TestCase):
    def test_process_is_in_job_immediately_after_atomic_launch(self):
        script = "import time; time.sleep(0.1)"
        process, job = spawn_process_with_job(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.assertIsNotNone(job)
            self.assertTrue(_windows_process_in_job(process, job.handle_value))
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if job is not None:
                job.close()

    def test_closing_job_terminates_child_and_grandchild(self):
        with tempfile.TemporaryDirectory() as temp:
            grandchild_pid_file = Path(temp) / "grandchild.pid"
            grandchild_script = "import time; time.sleep(60)"
            parent_script = (
                "import subprocess, sys, time; from pathlib import Path; "
                f"p=subprocess.Popen([sys.executable, '-c', {grandchild_script!r}]); "
                f"Path({str(grandchild_pid_file)!r}).write_text(str(p.pid), encoding='ascii'); "
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
                while not grandchild_pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(grandchild_pid_file.exists(), "The child did not report its grandchild PID.")
                grandchild_pid = int(grandchild_pid_file.read_text(encoding="ascii"))
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

    def test_forced_supervisor_kill_closes_job_and_kills_descendants(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child_pid_file = root / "child.pid"
            grandchild_pid_file = root / "grandchild.pid"
            grandchild_script = "import time; time.sleep(60)"
            child_script = (
                "import subprocess, sys, time; from pathlib import Path; "
                f"p=subprocess.Popen([sys.executable, '-c', {grandchild_script!r}]); "
                f"Path({str(grandchild_pid_file)!r}).write_text(str(p.pid), encoding='ascii'); "
                "time.sleep(60)"
            )
            supervisor_script = (
                "import subprocess, sys, time; from pathlib import Path; "
                "from narrow.windows_job import spawn_process_with_job; "
                f"p, job=spawn_process_with_job([sys.executable, '-c', {child_script!r}], "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
                f"Path({str(child_pid_file)!r}).write_text(str(p.pid), encoding='ascii'); "
                "time.sleep(60)"
            )
            repo_root = Path(__file__).resolve().parents[1]
            env = os.environ.copy()
            src_path = str(repo_root / "src")
            env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
            supervisor = subprocess.Popen(
                [sys.executable, "-c", supervisor_script],
                cwd=str(repo_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10
                while (
                    not child_pid_file.exists() or not grandchild_pid_file.exists()
                ) and time.monotonic() < deadline:
                    if supervisor.poll() is not None:
                        self.fail("The supervisor helper exited before reporting child PIDs.")
                    time.sleep(0.025)
                self.assertTrue(child_pid_file.exists(), "The helper did not report the Job child PID.")
                self.assertTrue(grandchild_pid_file.exists(), "The Job child did not report its grandchild PID.")
                child_pid = int(child_pid_file.read_text(encoding="ascii"))
                grandchild_pid = int(grandchild_pid_file.read_text(encoding="ascii"))

                # Deliberately omit /T: only the process holding the Job handle
                # is killed. KILL_ON_JOB_CLOSE must tear down both descendants.
                result = subprocess.run(
                    ["taskkill", "/PID", str(supervisor.pid), "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                supervisor.wait(timeout=5)
                self.assertTrue(_windows_process_exited(child_pid, timeout_ms=5000))
                self.assertTrue(_windows_process_exited(grandchild_pid, timeout_ms=5000))
            finally:
                if supervisor.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(supervisor.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                    try:
                        supervisor.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        supervisor.kill()
                        supervisor.wait(timeout=5)


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


def _windows_process_in_job(process: subprocess.Popen[bytes], job_handle: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    member = wintypes.BOOL()
    if not kernel32.IsProcessInJob(
        wintypes.HANDLE(int(process._handle)),
        wintypes.HANDLE(job_handle),
        ctypes.byref(member),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(member.value)


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
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such process.
    try:
        return kernel32.WaitForSingleObject(handle, timeout_ms) == wait_object_0
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
