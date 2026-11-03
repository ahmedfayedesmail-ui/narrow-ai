from __future__ import annotations

import re
import stat
from pathlib import Path
from typing import Any

from .container_hooks import import_container_hook_events
from .container_lifecycle import (
    cleanup_execution_agent_containers,
    cleanup_execution_agent_networks,
    list_execution_agent_containers,
)
from .core import NarrowError, Store
from .credential_broker import revoke_execution_credential_files
from .policy_manifest import _validate_execution_id

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")


def recover_agent_execution(
    store: Store,
    task_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Reconcile one explicitly selected stale execution after host interruption.

    The caller must select the exact task and execution. This is not a broad
    garbage collector and must not be run against a currently active run.
    """
    if not isinstance(store, Store):
        raise NarrowError("Runtime recovery requires the active NARROW task store.")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Invalid task ID for runtime recovery.")
    execution = _validate_execution_id(execution_id)
    store._check_state_paths()
    store.get_task(task_id)

    root = store.state_dir / "runtime" / "agent"
    task_root = root / task_id
    execution_root = task_root / execution
    try:
        for directory in (store.state_dir, store.state_dir / "runtime", root, task_root, execution_root):
            metadata = directory.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise NarrowError("Runtime recovery directory chain contains a link or non-directory.")
    except OSError as exc:
        raise NarrowError("Selected execution has no complete NARROW runtime state directory.") from exc

    removed_containers = cleanup_execution_agent_containers("docker", task_id, execution)
    remaining = list_execution_agent_containers("docker", task_id, execution)
    if remaining:
        raise NarrowError("Runtime recovery stopped: a task/execution-owned agent container is still present.")

    home = execution_root / "home"
    spool = home / ".local" / "state" / "narrow" / "hook-events.jsonl"
    imported_events = 0
    if spool.exists() or spool.is_symlink():
        imported_events = import_container_hook_events(store, task_id, execution, spool)

    revoked_credentials = revoke_execution_credential_files(store, task_id, execution)

    cidfile = execution_root / "container.cid"
    removed_cidfile = False
    if cidfile.exists() or cidfile.is_symlink():
        metadata = cidfile.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > 128
        ):
            raise NarrowError("Runtime recovery refuses an unsafe Docker container-ID record.")
        container_id = cidfile.read_text(encoding="ascii").strip()
        if container_id and not _CONTAINER_ID.fullmatch(container_id):
            raise NarrowError("Runtime recovery found a malformed Docker container ID; manual inspection is required.")
        cidfile.unlink()
        removed_cidfile = True

    remaining = list_execution_agent_containers("docker", task_id, execution)
    if remaining:
        raise NarrowError("Runtime recovery stopped: an agent container reappeared during cleanup.")
    removed_networks = cleanup_execution_agent_networks("docker", task_id, execution)

    report = {
        "execution_id": execution,
        "removed_containers": removed_containers,
        "removed_networks": removed_networks,
        "revoked_credentials": revoked_credentials,
        "imported_untrusted_hook_events": imported_events,
        "removed_cidfile": removed_cidfile,
        "status": "recovered",
    }
    store.add_event(task_id, "CONTAINER_EXECUTION_RECOVERED", report)
    return report
