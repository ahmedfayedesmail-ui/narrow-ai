"""Fail-closed Windows Job Object supervision for subprocess children.

The Job Object is attached through PROC_THREAD_ATTRIBUTE_JOB_LIST as part of
CreateProcessW. This removes the create-then-assign race: the child is born in
the kill-on-close Job, not briefly alive outside it.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from typing import Any

from .core import NarrowError

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
ERROR_INSUFFICIENT_BUFFER = 122


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


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoExW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfoW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _kernel32() -> Any:
    if os.name != "nt":
        raise NarrowError("Windows Job Objects are available only on Windows.")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _configure_job_apis(kernel32: Any) -> None:
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL


class WindowsJob:
    """Own a non-inheritable Job Object; closing it kills all associated processes."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle
        self._kernel32 = _kernel32()

    @property
    def handle_value(self) -> int:
        if not self._handle:
            raise NarrowError("The Windows Job Object handle is already closed.")
        return int(self._handle)

    @classmethod
    def create(cls) -> "WindowsJob":
        kernel32 = _kernel32()
        _configure_job_apis(kernel32)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise NarrowError(f"Could not create Windows Job Object (WinError {ctypes.get_last_error()}).")
        try:
            # The Job handle must remain private to the NARROW supervisor.
            if not kernel32.SetHandleInformation(handle, 0x00000001, 0):
                raise NarrowError(
                    f"Could not make Job Object handle non-inheritable "
                    f"(WinError {ctypes.get_last_error()})."
                )
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
            # Do not enable BREAKAWAY_OK or SILENT_BREAKAWAY_OK.
            return cls(handle)
        except BaseException:
            kernel32.CloseHandle(handle)
            raise

    def terminate(self, exit_code: int = 1) -> None:
        if not self._handle:
            return
        if not self._kernel32.TerminateJobObject(self._handle, exit_code):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle and not self._kernel32.CloseHandle(handle):
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


class _AtomicJobPopen(subprocess.Popen[Any]):
    """Popen-compatible launcher that puts the child in a Job at creation.

    CPython's public STARTUPINFO.lpAttributeList currently exposes only a
    limited set of attributes. We therefore call CreateProcessW directly for
    this one Windows-specific boundary while retaining Popen's pipe creation,
    stream wrapping, polling, timeout, and lifecycle behavior.
    """

    def __init__(self, *args: Any, narrow_job_handle: int, **kwargs: Any) -> None:
        self._narrow_job_handle = narrow_job_handle
        super().__init__(*args, **kwargs)

    def _execute_child(self, *parameters: Any) -> None:  # CPython Popen internal hook
        if len(parameters) < 16:
            raise RuntimeError("Unsupported CPython Popen._execute_child signature; refusing an unsafe Windows launch.")
        (
            args, executable, preexec_fn, close_fds, pass_fds, cwd, env,
            startupinfo, creationflags, shell, p2cread, p2cwrite,
            c2pread, c2pwrite, errread, errwrite,
        ) = parameters[:16]

        if preexec_fn is not None or pass_fds:
            raise ValueError("NARROW atomic Windows launch does not support preexec_fn or pass_fds.")
        if shell:
            raise ValueError("NARROW atomic Windows launch requires shell=False.")
        if not close_fds:
            raise ValueError("NARROW atomic Windows launch requires close_fds=True.")

        if isinstance(args, str):
            command_line = args
        elif isinstance(args, bytes):
            command_line = subprocess.list2cmdline([os.fsdecode(args)])
        elif isinstance(args, os.PathLike):
            command_line = subprocess.list2cmdline([os.fsdecode(args)])
        else:
            command_line = subprocess.list2cmdline([os.fsdecode(value) for value in args])
        if executable is not None:
            executable = os.fsdecode(executable)
        if cwd is not None:
            cwd = os.fsdecode(cwd)

        if startupinfo is None:
            startupinfo = subprocess.STARTUPINFO()
        else:
            startupinfo = startupinfo.copy()

        use_std_handles = -1 not in (p2cread, c2pwrite, errwrite)
        if use_std_handles:
            startupinfo.dwFlags |= STARTF_USESTDHANDLES
            startupinfo.hStdInput = p2cread
            startupinfo.hStdOutput = c2pwrite
            startupinfo.hStdError = errwrite

        attributes = startupinfo.lpAttributeList or {}
        unknown_attributes = set(attributes) - {"handle_list"}
        if unknown_attributes:
            raise ValueError(
                "Unsupported STARTUPINFO attribute(s) for atomic Job launch: "
                + ", ".join(sorted(unknown_attributes))
            )
        inherited_handles = list(attributes.get("handle_list", []))
        if use_std_handles:
            inherited_handles.extend((int(p2cread), int(c2pwrite), int(errwrite)))
        inherited_handles = self._filter_handle_list(inherited_handles)
        inherited_handles = list(dict.fromkeys(inherited_handles))

        # The handle-list attribute is required alongside JOB_LIST whenever
        # stdio pipes are inherited and close_fds=True. Both are installed in
        # the same STARTUPINFOEX list before CreateProcessW.
        attribute_count = 1 + int(bool(inherited_handles))
        kernel32 = _kernel32()
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)
        ]
        kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.DeleteProcThreadAttributeList.restype = None
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoW), ctypes.POINTER(_ProcessInformation),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.DuplicateHandle.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        kernel32.DuplicateHandle.restype = wintypes.BOOL

        attribute_size = ctypes.c_size_t()
        # The first call is documented to fail with ERROR_INSUFFICIENT_BUFFER.
        initialized = kernel32.InitializeProcThreadAttributeList(
            None, attribute_count, 0, ctypes.byref(attribute_size)
        )
        size_error = ctypes.get_last_error()
        if initialized or size_error != ERROR_INSUFFICIENT_BUFFER or attribute_size.value == 0:
            raise OSError(
                size_error or ERROR_INSUFFICIENT_BUFFER,
                "Could not size the Windows process attribute list",
            )

        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        attribute_pointer = ctypes.cast(attribute_buffer, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(
            attribute_pointer, attribute_count, 0, ctypes.byref(attribute_size)
        ):
            raise OSError(ctypes.get_last_error(), "InitializeProcThreadAttributeList failed")

        process_info = _ProcessInformation()
        job_handles = (wintypes.HANDLE * 1)(self._narrow_job_handle)
        environment_buffer = None
        duplicated_handles: dict[int, int] = {}
        inherited_array = None
        try:
            # Duplicate only explicit child stdio handles as inheritable handles.
            # This avoids temporarily flipping inheritance on shared originals
            # while another thread may create a process.
            current_process = kernel32.GetCurrentProcess()
            for original_handle in inherited_handles:
                original_value = int(original_handle)
                if original_value in duplicated_handles:
                    continue
                duplicate = wintypes.HANDLE()
                if not kernel32.DuplicateHandle(
                    current_process, wintypes.HANDLE(original_value),
                    current_process, ctypes.byref(duplicate), 0, True, 0x00000002,
                ):
                    raise OSError(ctypes.get_last_error(), "Could not duplicate a child stdio handle")
                duplicated_handles[original_value] = int(duplicate.value)
            if inherited_handles:
                inherited_array = (wintypes.HANDLE * len(inherited_handles))(
                    *(duplicated_handles[int(value)] for value in inherited_handles)
                )

            if not kernel32.UpdateProcThreadAttribute(
                attribute_pointer, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.cast(job_handles, ctypes.c_void_p), ctypes.sizeof(job_handles), None, None,
            ):
                raise OSError(ctypes.get_last_error(), "Could not add the Job List process attribute")
            if inherited_array is not None:
                if not kernel32.UpdateProcThreadAttribute(
                    attribute_pointer, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                    ctypes.cast(inherited_array, ctypes.c_void_p), ctypes.sizeof(inherited_array), None, None,
                ):
                    raise OSError(ctypes.get_last_error(), "Could not add the standard-handle inheritance attribute")

            startup = _StartupInfoExW()
            startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoExW)
            startup.StartupInfo.dwFlags = int(startupinfo.dwFlags)
            startup.StartupInfo.wShowWindow = int(startupinfo.wShowWindow)
            if use_std_handles:
                startup.StartupInfo.hStdInput = duplicated_handles.get(int(p2cread), int(p2cread))
                startup.StartupInfo.hStdOutput = duplicated_handles.get(int(c2pwrite), int(c2pwrite))
                startup.StartupInfo.hStdError = duplicated_handles.get(int(errwrite), int(errwrite))
            startup.lpAttributeList = attribute_pointer

            if env is None:
                environment_pointer = None
            else:
                environment_items: list[str] = []
                for key, value in env.items():
                    key_text, value_text = os.fsdecode(key), os.fsdecode(value)
                    if not key_text or "=" in key_text[1:] or "\x00" in key_text or "\x00" in value_text:
                        raise ValueError("Invalid Windows environment variable in child environment")
                    environment_items.append(f"{key_text}={value_text}")
                environment_text = "\x00".join(sorted(environment_items, key=str.casefold)) + "\x00\x00"
                environment_buffer = ctypes.create_unicode_buffer(environment_text)
                environment_pointer = ctypes.cast(environment_buffer, ctypes.c_void_p)

            command_buffer = ctypes.create_unicode_buffer(command_line)
            sys.audit("subprocess.Popen", executable, args, cwd, env)
            created = kernel32.CreateProcessW(
                executable,
                command_buffer,
                None,
                None,
                bool(inherited_handles),
                int(creationflags) | EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                environment_pointer,
                cwd,
                ctypes.cast(ctypes.byref(startup), ctypes.POINTER(_StartupInfoW)),
                ctypes.byref(process_info),
            )
            if not created:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.DeleteProcThreadAttributeList(attribute_pointer)
            for duplicate_handle in duplicated_handles.values():
                kernel32.CloseHandle(wintypes.HANDLE(duplicate_handle))
            self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)

        self._child_created = True
        self._handle = subprocess.Handle(int(process_info.hProcess))
        self.pid = int(process_info.dwProcessId)
        kernel32.CloseHandle(process_info.hThread)


def spawn_process_with_job(
    command: Any,
    *args: Any,
    **kwargs: Any,
) -> tuple[subprocess.Popen[Any], WindowsJob | None]:
    """Spawn a process; on Windows, make Job membership atomic at creation.

    POSIX keeps normal Popen behavior. Windows uses CreateProcessW with
    PROC_THREAD_ATTRIBUTE_JOB_LIST and STARTUPINFOEXW. If the OS cannot create
    the child in the Job, launch fails closed; there is no unmanaged fallback.
    """
    if os.name != "nt":
        return subprocess.Popen(command, *args, **kwargs), None

    job = WindowsJob.create()
    try:
        process = _AtomicJobPopen(
            command, *args, narrow_job_handle=job.handle_value, **kwargs
        )
        return process, job
    except BaseException:
        # If CreateProcessW succeeded before a later Python setup error, closing
        # the kill-on-close Job still tears down the child and descendants.
        try:
            job.terminate()
        except Exception:
            pass
        try:
            job.close()
        except Exception:
            pass
        raise
