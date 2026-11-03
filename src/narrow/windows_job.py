"""Fail-closed Windows Job Object supervision for subprocess.Popen children.

Windows children are created suspended, assigned to a kill-on-close Job Object,
and only then resumed. The CREATE_BREAKAWAY_FROM_JOB flag is deliberately
never used: it opts a process out of its parent's Job Object rather than
preventing descendants from escaping.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from typing import Any

from .core import NarrowError

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
CREATE_SUSPENDED = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
TH32CS_SNAPTHREAD = 0x00000004
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_RESUME_FAILED = 0xFFFFFFFF


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _kernel32() -> Any:
    if os.name != "nt":
        raise NarrowError("Windows Job Objects are available only on Windows.")
    return ctypes.WinDLL("kernel32", use_last_error=True)


class WindowsJob:
    """Own a non-inheritable Job Object; closing it kills all associated processes."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle
        self._kernel32 = _kernel32()

    @classmethod
    def create(cls) -> "WindowsJob":
        kernel32 = _kernel32()
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise NarrowError(f"Could not create Windows Job Object (WinError {ctypes.get_last_error()}).")
        try:
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise NarrowError(
                    f"Could not configure Windows Job Object (WinError {ctypes.get_last_error()})."
                )
            # Default CreateJobObject security attributes make this handle
            # non-inheritable. Do not enable BREAKAWAY_OK or SILENT_BREAKAWAY_OK.
            return cls(handle)
        except Exception:
            kernel32.CloseHandle(handle)
            raise

    def assign_suspended(self, process: subprocess.Popen[Any]) -> None:
        """Assign a still-suspended Popen child before its first instruction runs."""
        if process.poll() is not None:
            raise NarrowError("The child process exited before Job Object assignment.")
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise NarrowError(
                f"Could not assign suspended child to Windows Job Object "
                f"(WinError {ctypes.get_last_error()})."
            )

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel32.CloseHandle.restype = wintypes.BOOL
            if not self._kernel32.CloseHandle(handle):
                raise OSError(ctypes.get_last_error(), "CloseHandle(Job Object) failed")

    def __enter__(self) -> "WindowsJob":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _resume_primary_thread(pid: int) -> None:
    """Find and resume the one initial thread of a process created suspended.

    The target has not executed user-mode code, so it cannot have created
    descendants or additional threads before this point.
    """
    kernel32 = _kernel32()
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise NarrowError(f"Could not snapshot Windows threads (WinError {ctypes.get_last_error()}).")
    thread_id: int | None = None
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(_ThreadEntry32)
        found = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == pid:
                if thread_id is not None:
                    raise NarrowError("A suspended child unexpectedly has more than one thread; refusing to resume it.")
                thread_id = int(entry.th32ThreadID)
            found = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    if thread_id is None:
        raise NarrowError("Could not find the suspended child's primary thread.")

    thread = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, thread_id)
    if not thread:
        raise NarrowError(f"Could not open the suspended primary thread (WinError {ctypes.get_last_error()}).")
    try:
        previous_count = int(kernel32.ResumeThread(thread))
        if previous_count == _RESUME_FAILED:
            raise NarrowError(f"Could not resume the supervised child (WinError {ctypes.get_last_error()}).")
        if previous_count != 1:
            raise NarrowError(
                "The child's primary thread did not have the expected single suspend count; "
                "the process will be terminated rather than resumed ambiguously."
            )
    finally:
        kernel32.CloseHandle(thread)


def spawn_process_with_job(
    command: Any,
    *args: Any,
    **kwargs: Any,
) -> tuple[subprocess.Popen[Any], WindowsJob | None]:
    """Spawn a process; on Windows, assign it to a Job before allowing execution.

    The function preserves subprocess.Popen's standard pipe, cwd, env, and
    command handling. On Windows it adds CREATE_SUSPENDED, assigns the child,
    then resumes exactly one primary thread. Any setup failure kills the child
    and closes the Job; there is no unmanaged fallback.
    """
    if os.name != "nt":
        return subprocess.Popen(command, *args, **kwargs), None

    job = WindowsJob.create()
    process: subprocess.Popen[Any] | None = None
    original_flags = int(kwargs.get("creationflags", 0))
    kwargs["creationflags"] = original_flags | CREATE_SUSPENDED
    try:
        process = subprocess.Popen(command, *args, **kwargs)
        job.assign_suspended(process)
        _resume_primary_thread(process.pid)
        return process, job
    except Exception:
        # Closing the Job kills the child if assignment succeeded. If it did
        # not, terminate the suspended direct child explicitly.
        try:
            job.close()
        except Exception:
            pass
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise
