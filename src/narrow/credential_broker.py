"""Short-lived, task-scoped provider credential leases.

The broker copies exactly one explicitly selected provider API key from the
current process environment into a private, execution-specific host file.
The future launcher may mount that single file read-only. A credential inside
the agent container is still readable by the agent; this is host-profile
isolation, not protection from a malicious model or tool.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .core import NarrowError, Store
from .policy_manifest import _validate_execution_id

_PROVIDER_KEYS = {
    "claude": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY",),
}
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_SECRET_BYTES = 16 * 1024


@dataclass(frozen=True)
class CredentialLease:
    """Opaque reference to one short-lived provider credential file."""

    path: Path
    state_dir: Path
    provider: str
    environment_name: str
    task_id: str
    execution_id: str
    expires_at: float
    sha256: str


def _safe_directory(path: Path) -> None:
    try:
        if path.is_symlink():
            raise NarrowError("Credential broker directory is redirected by a symbolic link.")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise NarrowError("Credential broker path is not a real directory.")
        if os.name != "nt":
            os.chmod(path, 0o700)
            if stat.S_IMODE(path.stat().st_mode) != 0o700:
                raise NarrowError("Credential broker directory permissions could not be restricted.")
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError("Could not prepare the private credential broker directory.") from exc


def issue_credential_lease(
    store: Store,
    provider: str,
    task_id: str,
    execution_id: str,
    *,
    ttl_seconds: int = 900,
    environment: dict[str, str] | None = None,
) -> CredentialLease:
    """Materialize one explicit provider key without reading a global profile.

    POSIX owner-only file permissions are enforced. Windows is deliberately
    fail-closed until a native restricted-DACL implementation is available.
    """
    if os.name == "nt":
        raise NarrowError(
            "Credential file leases are disabled on Windows until NARROW can enforce and verify a restricted file DACL."
        )
    if provider not in _PROVIDER_KEYS:
        raise NarrowError("Credential broker supports only the native claude and codex provider keys.")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Invalid task ID for credential lease.")
    execution = _validate_execution_id(execution_id)
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 30 <= ttl_seconds <= 3600
    ):
        raise NarrowError("Credential lease TTL must be between 30 and 3600 seconds.")

    source = os.environ if environment is None else environment
    names = _PROVIDER_KEYS[provider]
    env_name = next((name for name in names if isinstance(source.get(name), str) and source.get(name)), None)
    if env_name is None:
        raise NarrowError(
            f"No explicit {provider} API key is available. Set {names[0]} in the host environment; NARROW will not inspect provider login profiles."
        )
    value = source[env_name]
    if not isinstance(value, str) or "\x00" in value:
        raise NarrowError("Provider credential must be text without NUL bytes.")
    try:
        payload = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NarrowError("Provider credential is not valid UTF-8 text.") from exc
    if not payload or len(payload) > _MAX_SECRET_BYTES or b"\n" in payload or b"\r" in payload:
        raise NarrowError("Provider credential must be a single-line value no larger than 16 KiB.")

    store._check_state_paths()
    root = store.state_dir / "runtime" / "agent" / task_id / execution / "credentials"
    # Validate each existing path component before creating the next one.
    current = store.state_dir
    for component in ("runtime", "agent", task_id, execution, "credentials"):
        current = current / component
        _safe_directory(current)

    lease_id = uuid.uuid4().hex
    destination = root / f"{provider}-{lease_id}.secret"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(destination, flags, 0o400)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size != len(payload)
        ):
            raise NarrowError("Credential broker could not verify the new secret file's ownership and permissions.")
    except NarrowError:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise NarrowError("Could not create a private provider credential lease.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    return CredentialLease(
        path=destination,
        state_dir=store.state_dir,
        provider=provider,
        environment_name=env_name,
        task_id=task_id,
        execution_id=execution,
        expires_at=time.time() + ttl_seconds,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def revoke_credential_lease(lease: CredentialLease) -> None:
    """Delete one validated lease file; never recursively remove its directory."""
    if not isinstance(lease, CredentialLease):
        raise NarrowError("Invalid credential lease handle.")
    if os.name == "nt":
        raise NarrowError("Credential file lease revocation is disabled on Windows.")
    if (
        not isinstance(lease.state_dir, Path)
        or lease.state_dir.name != ".narrow"
        or not isinstance(lease.path, Path)
        or lease.provider not in _PROVIDER_KEYS
        or not _TASK_ID.fullmatch(lease.task_id)
        or _validate_execution_id(lease.execution_id) != lease.execution_id
    ):
        raise NarrowError("Credential lease identity or NARROW state root is invalid.")
    prefix = f"{lease.provider}-"
    expected_root = lease.state_dir / "runtime" / "agent" / lease.task_id / lease.execution_id / "credentials"
    if (
        lease.path.absolute() != (expected_root / lease.path.name).absolute()
        or not lease.path.name.startswith(prefix)
        or not re.fullmatch(r"[0-9a-f]{32}\.secret", lease.path.name[len(prefix):])
    ):
        raise NarrowError("Credential lease path does not match its task/execution identity.")
    try:
        for directory in (
            lease.state_dir,
            lease.state_dir / "runtime",
            lease.state_dir / "runtime" / "agent",
            lease.state_dir / "runtime" / "agent" / lease.task_id,
            lease.state_dir / "runtime" / "agent" / lease.task_id / lease.execution_id,
            expected_root,
        ):
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise NarrowError("Credential lease directory chain contains a link or non-directory.")
        metadata = lease.path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise NarrowError("Refusing to revoke a linked or non-regular credential path.")
        if metadata.st_size > _MAX_SECRET_BYTES:
            raise NarrowError("Credential lease file exceeds the safety limit.")
        lease.path.unlink()
    except FileNotFoundError:
        return
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError("Could not safely revoke the provider credential lease.") from exc


def validate_credential_lease(lease: CredentialLease, *, now: float | None = None) -> Path:
    """Validate lease expiry, path integrity, owner-only mode, and content digest."""
    if not isinstance(lease, CredentialLease):
        raise NarrowError("Invalid credential lease handle.")
    current_time = time.time() if now is None else now
    if not isinstance(current_time, (int, float)) or isinstance(current_time, bool) or not math.isfinite(current_time):
        raise NarrowError("Credential lease validation time is invalid.")
    if (
        not isinstance(lease.expires_at, (int, float))
        or isinstance(lease.expires_at, bool)
        or not math.isfinite(lease.expires_at)
    ):
        raise NarrowError("Credential lease expiry metadata is invalid.")
    if current_time >= lease.expires_at:
        revoke_credential_lease(lease)
        raise NarrowError("Provider credential lease has expired and was revoked.")
    if (
        lease.provider not in _PROVIDER_KEYS
        or lease.environment_name not in _PROVIDER_KEYS[lease.provider]
        or not _TASK_ID.fullmatch(lease.task_id)
        or _validate_execution_id(lease.execution_id) != lease.execution_id
        or not isinstance(lease.state_dir, Path)
        or lease.state_dir.name != ".narrow"
        or not isinstance(lease.path, Path)
        or lease.path.absolute() != (
            lease.state_dir / "runtime" / "agent" / lease.task_id
            / lease.execution_id / "credentials" / lease.path.name
        ).absolute()
        or not lease.path.name.startswith(f"{lease.provider}-")
        or not re.fullmatch(r"[0-9a-f]{32}\.secret", lease.path.name[len(lease.provider) + 1:])
    ):
        raise NarrowError("Credential lease metadata does not match a supported provider/task execution.")
    try:
        for directory in (
            lease.state_dir,
            lease.state_dir / "runtime",
            lease.state_dir / "runtime" / "agent",
            lease.state_dir / "runtime" / "agent" / lease.task_id,
            lease.state_dir / "runtime" / "agent" / lease.task_id / lease.execution_id,
            lease.state_dir / "runtime" / "agent" / lease.task_id / lease.execution_id / "credentials",
        ):
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise NarrowError("Credential lease directory chain contains a link or non-directory.")
        metadata = lease.path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_SECRET_BYTES
        ):
            raise NarrowError("Credential lease file is linked, oversized, or has unsafe permissions.")
        descriptor = os.open(lease.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or stat.S_IMODE(opened.st_mode) != 0o400
                or opened.st_size != metadata.st_size
            ):
                raise NarrowError("Credential lease file changed while it was being validated.")
            payload = bytearray()
            while len(payload) <= _MAX_SECRET_BYTES:
                chunk = os.read(descriptor, min(4096, _MAX_SECRET_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) != opened.st_size:
                raise NarrowError("Credential lease file changed or exceeded its read limit.")
        finally:
            os.close(descriptor)
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError("Could not safely validate provider credential lease.") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual, lease.sha256):
        raise NarrowError("Credential lease content does not match the issued digest.")
    return lease.path


def revoke_execution_credential_files(
    store: Store,
    task_id: str,
    execution_id: str,
) -> int:
    """Revoke every validated credential file for one explicitly recovered execution.

    This is an operator-invoked crash-recovery primitive. It never traverses
    outside the exact task/execution credentials directory.
    """
    if os.name == "nt":
        raise NarrowError("Execution credential recovery is disabled on Windows until restricted DACL support exists.")
    if not isinstance(store, Store):
        raise NarrowError("Credential recovery requires the active NARROW task store.")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Invalid task ID for credential recovery.")
    execution = _validate_execution_id(execution_id)
    store._check_state_paths()
    root = store.state_dir / "runtime" / "agent" / task_id / execution
    credentials = root / "credentials"
    try:
        for directory in (
            store.state_dir,
            store.state_dir / "runtime",
            store.state_dir / "runtime" / "agent",
            store.state_dir / "runtime" / "agent" / task_id,
            root,
        ):
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise NarrowError("Execution recovery directory chain contains a link or non-directory.")
        if not credentials.exists():
            return 0
        info = credentials.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise NarrowError("Execution credentials path is linked or not a directory.")
        removed = 0
        for entry in credentials.iterdir():
            match = re.fullmatch(r"(?:claude|codex)-[0-9a-f]{32}\\.secret", entry.name)
            if not match:
                raise NarrowError("Execution credentials directory contains an unexpected entry; refusing bulk cleanup.")
            metadata = entry.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o400
                or metadata.st_size <= 0
                or metadata.st_size > _MAX_SECRET_BYTES
            ):
                raise NarrowError("An execution credential file is linked, oversized, or has unsafe permissions.")
            entry.unlink()
            removed += 1
        return removed
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError("Could not safely recover task-scoped provider credential files.") from exc


