"""Minimal, task-scoped policy manifests for isolated agent runtimes.

This module deliberately does not expose the NARROW SQLite database or host
filesystem paths to an agent container. The manifest is data-only and is
mounted read-only by the future container launcher.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import NarrowError, Store, now_iso

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SCHEMA = "narrow.agent-policy.v1"
_MAX_MANIFEST_BYTES = 256 * 1024
_RESERVED_PATHS = [
    ".git",
    ".narrow",
    "NARROW_TASK.md",
    "NARROW_HANDOFF.md",
    "NARROW_PROMPT.md",
]


@dataclass(frozen=True)
class PolicyManifestFile:
    """Host-side reference to one immutable task/execution policy document."""

    path: Path
    task_id: str
    execution_id: str
    sha256: str


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NarrowError(f"Policy manifest contains a duplicate JSON key: {key!r}.")
        result[key] = value
    return result


def _validate_execution_id(execution_id: str) -> str:
    if not isinstance(execution_id, str) or len(execution_id) != 36:
        raise NarrowError("Execution ID must be a canonical UUID.")
    try:
        parsed = uuid.UUID(execution_id)
    except (ValueError, AttributeError) as exc:
        raise NarrowError("Execution ID must be a canonical UUID.") from exc
    if str(parsed) != execution_id.lower():
        raise NarrowError("Execution ID must be a canonical UUID.")
    return str(parsed)


def _validate_patterns(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or len(value) > 100
    ):
        raise NarrowError(f"Policy {field} must be a list of at most 100 repository-relative patterns.")
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 1000
            or "\x00" in item
        ):
            raise NarrowError(f"Policy {field} contains an invalid path pattern.")
        normalized = item.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise NarrowError(f"Policy {field} must not contain absolute paths or parent traversal.")
        result.append(item)
    return result


def _build_manifest(task: dict[str, Any], execution_id: str) -> dict[str, Any]:
    task_id = task.get("id")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Task record contains an invalid task ID.")

    allowed = _validate_patterns(task.get("allowed"), "allowed_paths")
    denied = _validate_patterns(task.get("denied"), "denied_paths", allow_empty=True)

    acceptance_value = task.get("acceptance")
    if not isinstance(acceptance_value, list) or not 1 <= len(acceptance_value) <= 100:
        raise NarrowError("Task must contain 1–100 acceptance criteria for its policy manifest.")
    acceptance: list[dict[str, Any]] = []
    for criterion in acceptance_value:
        if not isinstance(criterion, dict):
            raise NarrowError("Task acceptance criteria are malformed.")
        text = criterion.get("text")
        accepted = criterion.get("accepted", False)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000 or "\x00" in text:
            raise NarrowError("Task acceptance criteria contain invalid text.")
        if not isinstance(accepted, bool):
            raise NarrowError("Task acceptance status must be boolean.")
        acceptance.append({"text": text, "accepted": accepted})

    title = task.get("title")
    goal = task.get("goal")
    if not isinstance(title, str) or len(title) > 500 or not isinstance(goal, str) or len(goal) > 8000:
        raise NarrowError("Task title or goal is malformed.")

    return {
        "schema": _SCHEMA,
        "schema_version": 1,
        "generated_at": now_iso(),
        "task_id": task_id,
        "execution_id": execution_id,
        # The container sees a stable virtual path, never the host worktree.
        "workspace_root": "/workspace",
        "task": {"title": title, "goal": goal},
        "scope": {
            "allowed_paths": allowed,
            "denied_paths": denied,
            "reserved_paths": list(_RESERVED_PATHS),
            "deny_path_traversal": True,
            "deny_symlinks_and_hardlinks": True,
        },
        "acceptance_criteria": acceptance,
        "controls": {
            "write_scope_required": True,
            "stop_on_scope_violation": True,
            "human_acceptance_required": True,
            "host_supervisor_is_authoritative": True,
        },
    }


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise NarrowError(f"Policy runtime directory is redirected: {path}")
    if path.exists():
        if not path.is_dir():
            raise NarrowError(f"Policy runtime path is not a directory: {path}")
    else:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            if path.is_symlink() or not path.is_dir():
                raise NarrowError(f"Policy runtime directory changed during creation: {path}")
        except OSError as exc:
            raise NarrowError(f"Could not create policy runtime directory {path}: {exc}") from exc
    if path.is_symlink() or not path.is_dir():
        raise NarrowError(f"Policy runtime directory is unsafe: {path}")
    if os.name != "nt":
        try:
            os.chmod(path, 0o700)
        except OSError as exc:
            raise NarrowError(f"Could not restrict policy runtime directory permissions: {path}") from exc


def write_policy_manifest(
    store: Store,
    task_id: str,
    execution_id: str | None = None,
) -> PolicyManifestFile:
    """Create a task-scoped manifest outside the agent worktree.

    The document contains only task policy and bounded task context. It never
    serializes Store, SQLite rows, host paths, environment variables, or keys.
    """
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Invalid task ID for policy manifest generation.")
    execution = _validate_execution_id(execution_id or str(uuid.uuid4()))

    store._check_state_paths()
    task = store.get_task(task_id)
    manifest = _build_manifest(task, execution)
    payload = _canonical_json(manifest)
    if len(payload) > _MAX_MANIFEST_BYTES:
        raise NarrowError("Generated policy manifest exceeds the 256 KiB limit.")
    digest = hashlib.sha256(payload).hexdigest()
    envelope = {
        "manifest": manifest,
        "integrity": {"algorithm": "sha256", "digest": digest},
    }
    serialized = _canonical_json(envelope) + b"\n"

    runtime_root = store.state_dir / "runtime"
    directory_chain = [
        runtime_root,
        runtime_root / "agent",
        runtime_root / "agent" / task_id,
        runtime_root / "agent" / task_id / execution,
    ]
    for directory in directory_chain:
        _ensure_private_directory(directory)

    destination = directory_chain[-1] / "policy.json"
    if destination.exists() or destination.is_symlink():
        raise NarrowError("A policy manifest already exists for this execution ID; refusing to overwrite it.")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=".policy-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        if destination.exists() or destination.is_symlink():
            raise NarrowError("Policy manifest destination changed during creation.")
        os.replace(temporary, destination)
        temporary = None
        if os.name != "nt":
            os.chmod(destination, stat.S_IRUSR)
        return PolicyManifestFile(destination, task_id, execution, digest)
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError(f"Could not write the task policy manifest: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def read_policy_manifest(
    path: Path,
    *,
    expected_task_id: str | None = None,
    expected_execution_id: str | None = None,
) -> dict[str, Any]:
    """Read and validate a manifest; SHA-256 detects corruption, not forgery.

    The container launcher must mount this file read-only and the host
    supervisor remains authoritative. This checksum is intentionally not
    described as a cryptographic signature.
    """
    source = Path(path)
    try:
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise NarrowError("Policy manifest must be a regular, unlinked file.")
        if metadata.st_size > _MAX_MANIFEST_BYTES:
            raise NarrowError("Policy manifest exceeds the 256 KiB safety limit.")
        raw = source.read_bytes()
        envelope = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except NarrowError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NarrowError(f"Could not safely read policy manifest: {exc}") from exc

    if not isinstance(envelope, dict) or set(envelope) != {"manifest", "integrity"}:
        raise NarrowError("Policy manifest envelope is malformed.")
    manifest = envelope["manifest"]
    integrity = envelope["integrity"]
    if not isinstance(manifest, dict) or not isinstance(integrity, dict):
        raise NarrowError("Policy manifest payload is malformed.")
    if integrity.get("algorithm") != "sha256":
        raise NarrowError("Policy manifest integrity algorithm is unsupported.")
    digest = integrity.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise NarrowError("Policy manifest integrity digest is malformed.")
    actual = hashlib.sha256(_canonical_json(manifest)).hexdigest()
    if not hmac.compare_digest(actual, digest):
        raise NarrowError("Policy manifest checksum mismatch; refusing to use corrupted policy.")
    if manifest.get("schema") != _SCHEMA or manifest.get("schema_version") != 1:
        raise NarrowError("Policy manifest schema is unsupported.")
    task_id = manifest.get("task_id")
    execution_id = manifest.get("execution_id")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Policy manifest task ID is invalid.")
    _validate_execution_id(execution_id)
    if manifest.get("workspace_root") != "/workspace":
        raise NarrowError("Policy manifest workspace root is invalid.")
    if expected_task_id is not None and task_id != expected_task_id:
        raise NarrowError("Policy manifest belongs to a different task.")
    if expected_execution_id is not None and execution_id != expected_execution_id:
        raise NarrowError("Policy manifest belongs to a different execution.")
    # Re-validate the core policy collections when reading untrusted bytes.
    scope = manifest.get("scope")
    if not isinstance(scope, dict):
        raise NarrowError("Policy manifest scope is malformed.")
    _validate_patterns(scope.get("allowed_paths"), "allowed_paths")
    _validate_patterns(scope.get("denied_paths"), "denied_paths", allow_empty=True)
    return manifest
