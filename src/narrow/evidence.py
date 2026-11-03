"""Local HMAC signing for verification evidence.

Keys live in the user's NARROW cache, outside the task worktree and repository.
This detects database-only tampering; it is not a remote timestamp or protection
against a compromised host account that can read/replace the local key.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from pathlib import Path
from typing import Any

from .core import NarrowError, cache_root, redact


def _key_path(project_root: Path) -> Path:
    resolved = Path(project_root).resolve(strict=True)
    project_id = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:32]
    return cache_root() / "evidence-keys" / f"{project_id}.key"


def _load_or_create_key(project_root: Path) -> bytes:
    path = _key_path(project_root)
    directory = path.parent
    if directory.is_symlink():
        raise NarrowError("NARROW evidence key directory must not be a symbolic link.")
    try:
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(directory, 0o700)
        if path.is_symlink():
            raise NarrowError("NARROW evidence signing key is a symbolic link.")
        binary_flag = getattr(os, "O_BINARY", 0)
        flags = os.O_RDONLY | binary_flag | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary_flag | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(path, create_flags, 0o600)
            except FileExistsError:
                fd = os.open(path, flags)
            else:
                key = secrets.token_bytes(32)
                try:
                    view = memoryview(key)
                    offset = 0
                    while offset < len(view):
                        written = os.write(fd, view[offset:])
                        if written <= 0:
                            raise OSError("Could not write the complete evidence key.")
                        offset += written
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if os.name != "nt":
                    os.chmod(path, 0o600)
                return key
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise NarrowError("NARROW evidence signing key is not a private regular file.")
            if os.name != "nt" and metadata.st_mode & 0o077:
                raise NarrowError("NARROW evidence signing key permissions are too broad; expected mode 0600.")
            for attempt in range(50):
                metadata = os.fstat(fd)
                if metadata.st_size == 32:
                    break
                if metadata.st_size > 32:
                    raise NarrowError("NARROW evidence signing key has an invalid length.")
                time.sleep(0.01)
            if os.fstat(fd).st_size != 32:
                raise NarrowError("NARROW evidence signing key is incomplete.")
            key = os.read(fd, 33)
            if len(key) != 32:
                raise NarrowError("NARROW evidence signing key has an invalid length.")
            return key
        finally:
            os.close(fd)
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError(f"Could not open the local NARROW evidence signing key: {exc}") from exc


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign_check_evidence(project_root: Path, payload: dict[str, Any]) -> str:
    key = _load_or_create_key(project_root)
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def verify_check_evidence(project_root: Path, payload: dict[str, Any], signature: str) -> bool:
    if not isinstance(signature, str) or len(signature) != 64:
        return False
    try:
        expected = sign_check_evidence(project_root, payload)
    except NarrowError:
        return False
    return hmac.compare_digest(expected, signature)


def check_evidence_payload(
    *,
    check_run_id: int,
    task_id: str,
    check_name: str,
    snapshot_hash: str,
    command_json: str,
    exit_code: int,
    passed: bool,
    duration_ms: int,
    output_tail: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "schema": "narrow.check-evidence.v1",
        "check_run_id": int(check_run_id),
        "task_id": task_id,
        "check_name": check_name,
        "snapshot_hash": snapshot_hash,
        "command_json": command_json,
        "exit_code": int(exit_code),
        "passed": bool(passed),
        "duration_ms": int(duration_ms),
        "output_sha256": hashlib.sha256(redact(output_tail).encode("utf-8")).hexdigest(),
        "created_at": created_at,
    }
