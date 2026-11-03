"""Windows Job Object containment for supervised child processes.

The Job handle is intentionally non-inheritable. If the NARROW process exits or
crashes, Windows closes the handle and JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
terminates processes already assigned to the job.
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


class WindowsJob:
    """Own a Windows Job Object; closing it kills all associated processes."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    @classmethod
    def assign(cls, process: subprocess.Popen[Any]) -> "WindowsJob | None":
        if os.name != "nt":
            return None
        if process.poll() is not None:
            raise NarrowError("The child process exited before Windows Job Object assignment.")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise NarrowError(f"Could not create Windows Job Object (WinError {ctypes.get_last_error()}).")

        try:
            info = _ExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise NarrowError(f"Could not configure Windows Job Object (WinError {ctypes.get_last_error()}).")

            process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                raise NarrowError(f"Could not assign child to Windows Job Object (WinError {ctypes.get_last_error()}).")
            return cls(handle)
        except Exception:
            kernel32.CloseHandle(handle)
            # Fail closed for the direct child if containment cannot be installed.
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)

    def __enter__(self) -> "WindowsJob":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def assign_process_to_job(process: subprocess.Popen[Any]) -> WindowsJob | None:
    """Attach a child process to a kill-on-close Job Object on Windows."""
    return WindowsJob.assign(process)
