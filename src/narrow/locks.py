"""Per-task single-flight execution leases.

This coordinates NARROW processes. It is not an operating-system sandbox.
On POSIX, the first-level child inherits the open-file description so a lease
can survive an unexpected supervisor exit while that child is still alive.
"""
from __future__ import annotations

import errno
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .core import NarrowError

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@contextmanager
def task_execution_lock(state_dir: Path, task_id: str) -> Iterator[int]:
    """Acquire a non-blocking OS lock for one task."""
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Invalid task identifier for execution lock.")
    state_dir = Path(state_dir)
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise NarrowError("NARROW state directory is missing or redirected.")
    lock_dir = state_dir / "locks"
    if lock_dir.is_symlink() or (lock_dir.exists() and not lock_dir.is_dir()):
        raise NarrowError("NARROW lock directory is not a directory or is a symbolic link.")
    try:
        lock_dir.mkdir(mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(lock_dir, 0o700)
    except OSError as exc:
        raise NarrowError(f"Could not prepare the task lock directory: {exc}") from exc
    if lock_dir.is_symlink() or not lock_dir.is_dir():
        raise NarrowError("NARROW lock directory changed during setup.")
    lock_path = lock_dir / (task_id + ".lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise NarrowError(f"Could not open the task lock safely: {exc}") from exc

    acquired = False
    try:
        opened = os.fstat(fd)
        current = os.lstat(lock_path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_ISLNK(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise NarrowError("The task lock is linked or is not a private regular file.")
        if os.name == "nt":
            import msvcrt
            if opened.st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise NarrowError("This task already has an active NARROW operation. Wait for it to finish, or inspect the task before retrying.") from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise NarrowError("This task already has an active NARROW operation. Wait for it to finish, or inspect the task before retrying.") from exc
                raise NarrowError(f"Could not acquire the task execution lock: {exc}") from exc
        acquired = True
        yield fd
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def inherited_lock_options(lock_fd: int | None) -> Iterator[dict[str, object]]:
    """Keep the lease descriptor open in the first-level child process."""
    if lock_fd is None:
        yield {}
    elif os.name == "nt":
        try:
            os.set_inheritable(lock_fd, True)
        except OSError as exc:
            raise NarrowError(f"Could not pass the execution lease to the child: {exc}") from exc
        try:
            yield {"close_fds": False}
        finally:
            try:
                os.set_inheritable(lock_fd, False)
            except OSError:
                pass
    else:
        yield {"pass_fds": (lock_fd,)}
