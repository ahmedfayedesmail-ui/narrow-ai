"""Ownership-checked lifecycle operations for NARROW agent containers.

Only containers carrying the exact task/execution identity can be removed.
This module does not create containers or claim crash recovery by itself; the
future launcher must persist the execution identity before creating resources.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from .core import NarrowError
from .policy_manifest import _validate_execution_id

_LABEL_MANAGED = "io.narrow.managed"
_LABEL_COMPONENT = "io.narrow.component"
_LABEL_TASK = "io.narrow.task"
_LABEL_EXECUTION = "io.narrow.execution"
_LABEL_SCHEMA = "io.narrow.schema"
_SCHEMA_VALUE = "agent-runtime.v1"
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")


@dataclass(frozen=True)
class AgentContainer:
    """A Docker/Podman container proven to belong to one NARROW execution."""

    container_id: str
    name: str
    task_id: str
    execution_id: str
    status: str


def _identity(task_id: str, execution_id: str) -> tuple[str, str]:
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Invalid NARROW task ID for container lifecycle operation.")
    return task_id, _validate_execution_id(execution_id)


def agent_container_labels(task_id: str, execution_id: str) -> dict[str, str]:
    """Return immutable ownership labels to attach when creating an agent."""
    task, execution = _identity(task_id, execution_id)
    return {
        _LABEL_MANAGED: "true",
        _LABEL_COMPONENT: "agent",
        _LABEL_TASK: task,
        _LABEL_EXECUTION: execution,
        _LABEL_SCHEMA: _SCHEMA_VALUE,
    }


def agent_container_label_args(task_id: str, execution_id: str) -> list[str]:
    """Return argv-safe --label pairs for Docker/Podman create/run."""
    labels = agent_container_labels(task_id, execution_id)
    return [item for key, value in labels.items() for item in ("--label", f"{key}={value}")]


def _engine_path(engine: str) -> str:
    if not isinstance(engine, str) or engine not in {"docker", "podman"}:
        raise NarrowError("Container lifecycle engine must be docker or podman.")
    executable = shutil.which(engine)
    if not executable:
        raise NarrowError(f"{engine} was not found on PATH; no container lifecycle action was taken.")
    return executable


def _run(engine: str, args: list[str], *, timeout: int = 20) -> str:
    executable = _engine_path(engine)
    try:
        result = subprocess.run(
            [executable, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NarrowError(f"Could not complete container lifecycle operation: {type(exc).__name__}.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "container engine command failed").strip()
        raise NarrowError(f"Container lifecycle operation failed: {detail[:1000]}")
    return result.stdout


def _inspect(engine: str, container_id: str) -> dict[str, Any]:
    if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(container_id):
        raise NarrowError("Container ID must be a 12–64 character hexadecimal engine ID.")
    raw = _run(engine, ["container", "inspect", container_id])
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NarrowError("Container engine returned malformed inspect JSON.") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise NarrowError("Container engine returned an unexpected inspect response.")
    return value[0]


def _owned_container(
    engine: str,
    container_id: str,
    task_id: str,
    execution_id: str,
) -> AgentContainer:
    task, execution = _identity(task_id, execution_id)
    inspected = _inspect(engine, container_id)
    config = inspected.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    expected = agent_container_labels(task, execution)
    if not isinstance(labels, dict) or any(labels.get(key) != value for key, value in expected.items()):
        raise NarrowError(
            "Refusing to control this container: its NARROW managed/task/execution labels do not match exactly."
        )
    state = inspected.get("State")
    state_status = state.get("Status") if isinstance(state, dict) else "unknown"
    name = inspected.get("Name")
    if not isinstance(name, str) or not name.startswith("/"):
        raise NarrowError("Container engine returned an invalid container name.")
    return AgentContainer(
        container_id=container_id,
        name=name[1:],
        task_id=task,
        execution_id=execution,
        status=state_status if isinstance(state_status, str) else "unknown",
    )


def list_execution_agent_containers(
    engine: str,
    task_id: str,
    execution_id: str,
) -> list[AgentContainer]:
    """List only containers whose inspect metadata proves exact ownership."""
    task, execution = _identity(task_id, execution_id)
    ids_raw = _run(engine, [
        "container", "ls", "--all", "--quiet",
        "--filter", f"label={_LABEL_MANAGED}=true",
        "--filter", f"label={_LABEL_COMPONENT}=agent",
        "--filter", f"label={_LABEL_TASK}={task}",
        "--filter", f"label={_LABEL_EXECUTION}={execution}",
    ])
    ids = [line.strip() for line in ids_raw.splitlines() if line.strip()]
    if len(ids) > 32:
        raise NarrowError("Unexpectedly large number of agent containers for one execution; refusing automatic cleanup.")
    result: list[AgentContainer] = []
    seen: set[str] = set()
    for container_id in ids:
        if container_id in seen:
            continue
        seen.add(container_id)
        result.append(_owned_container(engine, container_id, task, execution))
    return result


def cleanup_execution_agent_containers(
    engine: str,
    task_id: str,
    execution_id: str,
) -> int:
    """Force-remove only agent containers whose full ownership labels match."""
    containers = list_execution_agent_containers(engine, task_id, execution_id)
    removed = 0
    for container in containers:
        # Re-inspect immediately before deletion to avoid acting on stale list data.
        _owned_container(engine, container.container_id, task_id, execution_id)
        _run(engine, ["container", "rm", "--force", container.container_id], timeout=30)
        removed += 1
    return removed
