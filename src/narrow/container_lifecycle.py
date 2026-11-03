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


def egress_proxy_container_labels(task_id: str, execution_id: str) -> dict[str, str]:
    """Return ownership labels for the execution's dedicated egress sidecar."""
    labels = agent_container_labels(task_id, execution_id)
    labels[_LABEL_COMPONENT] = "egress-proxy"
    return labels


def egress_proxy_container_label_args(task_id: str, execution_id: str) -> list[str]:
    labels = egress_proxy_container_labels(task_id, execution_id)
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
    *,
    component: str = "agent",
) -> AgentContainer:
    task, execution = _identity(task_id, execution_id)
    if component not in {"agent", "egress-proxy"}:
        raise NarrowError("Unsupported NARROW container component.")
    inspected = _inspect(engine, container_id)
    config = inspected.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    expected = (
        agent_container_labels(task, execution)
        if component == "agent"
        else egress_proxy_container_labels(task, execution)
    )
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


def list_execution_egress_proxy_containers(
    engine: str,
    task_id: str,
    execution_id: str,
) -> list[AgentContainer]:
    """List only the dedicated egress sidecars for one exact execution."""
    task, execution = _identity(task_id, execution_id)
    ids_raw = _run(engine, [
        "container", "ls", "--all", "--quiet",
        "--filter", f"label={_LABEL_MANAGED}=true",
        "--filter", f"label={_LABEL_COMPONENT}=egress-proxy",
        "--filter", f"label={_LABEL_TASK}={task}",
        "--filter", f"label={_LABEL_EXECUTION}={execution}",
    ])
    ids = [line.strip() for line in ids_raw.splitlines() if line.strip()]
    if len(ids) > 4:
        raise NarrowError("Unexpectedly large number of egress sidecars for one execution; refusing cleanup.")
    result: list[AgentContainer] = []
    seen: set[str] = set()
    for container_id in ids:
        if container_id in seen:
            continue
        seen.add(container_id)
        result.append(_owned_container(engine, container_id, task, execution, component="egress-proxy"))
    return result


def cleanup_execution_egress_proxy_containers(
    engine: str,
    task_id: str,
    execution_id: str,
) -> int:
    """Remove only sidecars whose complete execution ownership labels still match."""
    containers = list_execution_egress_proxy_containers(engine, task_id, execution_id)
    removed = 0
    for container in containers:
        _owned_container(engine, container.container_id, task_id, execution_id, component="egress-proxy")
        _run(engine, ["container", "rm", "--force", container.container_id], timeout=30)
        removed += 1
    return removed


@dataclass(frozen=True)
class AgentNetwork:
    """An internal, execution-scoped network reserved for one NARROW agent run."""

    network_id: str
    name: str
    task_id: str
    execution_id: str
    driver: str


def agent_network_labels(task_id: str, execution_id: str) -> dict[str, str]:
    """Return labels for a private, per-execution agent network."""
    task, execution = _identity(task_id, execution_id)
    return {
        _LABEL_MANAGED: "true",
        _LABEL_COMPONENT: "agent-network",
        _LABEL_TASK: task,
        _LABEL_EXECUTION: execution,
        _LABEL_SCHEMA: _SCHEMA_VALUE,
    }


def agent_network_label_args(task_id: str, execution_id: str) -> list[str]:
    labels = agent_network_labels(task_id, execution_id)
    return [item for key, value in labels.items() for item in ("--label", f"{key}={value}")]


def _inspect_network(engine: str, network_id: str) -> dict[str, Any]:
    if not isinstance(network_id, str) or not _CONTAINER_ID.fullmatch(network_id):
        raise NarrowError("Network ID must be a 12–64 character hexadecimal engine ID.")
    raw = _run(engine, ["network", "inspect", network_id])
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NarrowError("Container engine returned malformed network inspect JSON.") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise NarrowError("Container engine returned an unexpected network inspect response.")
    return value[0]


def _owned_network(
    engine: str,
    network_id: str,
    task_id: str,
    execution_id: str,
) -> AgentNetwork:
    task, execution = _identity(task_id, execution_id)
    inspected = _inspect_network(engine, network_id)
    labels = inspected.get("Labels")
    expected = agent_network_labels(task, execution)
    if not isinstance(labels, dict) or any(labels.get(key) != value for key, value in expected.items()):
        raise NarrowError(
            "Refusing to control this network: its NARROW managed/task/execution labels do not match exactly."
        )
    if inspected.get("Internal") is not True:
        raise NarrowError("Refusing to use or remove an agent network that is not Docker/Podman internal.")
    driver = inspected.get("Driver")
    name = inspected.get("Name")
    if driver != "bridge" or not isinstance(name, str) or not name.startswith("narrow-agent-"):
        raise NarrowError("Agent network must be a named internal bridge network.")
    return AgentNetwork(network_id, name, task, execution, driver)


def create_execution_agent_network(
    engine: str,
    task_id: str,
    execution_id: str,
) -> AgentNetwork:
    """Create an internal bridge network with immutable execution ownership labels."""
    task, execution = _identity(task_id, execution_id)
    name = f"narrow-agent-{task[:32]}-{execution.replace('-', '')[:12]}"
    command = [
        "network", "create", "--driver", "bridge", "--internal",
        *agent_network_label_args(task, execution),
        name,
    ]
    network_id = _run(engine, command, timeout=30).strip()
    network = _owned_network(engine, network_id, task, execution)
    if network.name != name:
        raise NarrowError("Container engine created a network with an unexpected name.")
    return network


def list_execution_agent_networks(
    engine: str,
    task_id: str,
    execution_id: str,
) -> list[AgentNetwork]:
    """List only internal agent networks whose inspect metadata proves ownership."""
    task, execution = _identity(task_id, execution_id)
    ids_raw = _run(engine, [
        "network", "ls", "--quiet",
        "--filter", f"label={_LABEL_MANAGED}=true",
        "--filter", f"label={_LABEL_COMPONENT}=agent-network",
        "--filter", f"label={_LABEL_TASK}={task}",
        "--filter", f"label={_LABEL_EXECUTION}={execution}",
    ])
    ids = [line.strip() for line in ids_raw.splitlines() if line.strip()]
    if len(ids) > 8:
        raise NarrowError("Unexpectedly large number of agent networks for one execution; refusing automatic cleanup.")
    result: list[AgentNetwork] = []
    seen: set[str] = set()
    for network_id in ids:
        if network_id in seen:
            continue
        seen.add(network_id)
        result.append(_owned_network(engine, network_id, task, execution))
    return result



def egress_uplink_network_labels(task_id: str, execution_id: str) -> dict[str, str]:
    """Return labels for the sidecar-only externally routed bridge."""
    labels = agent_network_labels(task_id, execution_id)
    labels[_LABEL_COMPONENT] = "egress-uplink"
    return labels


def egress_uplink_network_label_args(task_id: str, execution_id: str) -> list[str]:
    labels = egress_uplink_network_labels(task_id, execution_id)
    return [item for key, value in labels.items() for item in ("--label", f"{key}={value}")]


def create_execution_egress_uplink_network(
    engine: str,
    task_id: str,
    execution_id: str,
) -> AgentNetwork:
    """Create a dedicated routed bridge reserved for the egress proxy sidecar."""
    task, execution = _identity(task_id, execution_id)
    name = f"narrow-egress-uplink-{task[:24]}-{execution.replace('-', '')[:12]}"
    network_id = _run(engine, [
        "network", "create", "--driver", "bridge",
        *egress_uplink_network_label_args(task, execution), name,
    ], timeout=30).strip()
    inspected = _inspect_network(engine, network_id)
    labels = inspected.get("Labels")
    if not isinstance(labels, dict) or any(
        labels.get(key) != value for key, value in egress_uplink_network_labels(task, execution).items()
    ):
        raise NarrowError("Created egress uplink network does not carry exact NARROW ownership labels.")
    if inspected.get("Internal") is not False or inspected.get("Driver") != "bridge" or inspected.get("Name") != name:
        raise NarrowError("Egress uplink must be a dedicated, non-internal bridge network.")
    return AgentNetwork(network_id, name, task, execution, "bridge")


def list_execution_egress_uplink_networks(
    engine: str,
    task_id: str,
    execution_id: str,
) -> list[AgentNetwork]:
    task, execution = _identity(task_id, execution_id)
    ids_raw = _run(engine, [
        "network", "ls", "--quiet",
        "--filter", f"label={_LABEL_MANAGED}=true",
        "--filter", f"label={_LABEL_COMPONENT}=egress-uplink",
        "--filter", f"label={_LABEL_TASK}={task}",
        "--filter", f"label={_LABEL_EXECUTION}={execution}",
    ])
    ids = [line.strip() for line in ids_raw.splitlines() if line.strip()]
    if len(ids) > 4:
        raise NarrowError("Unexpectedly large number of egress uplink networks for one execution; refusing cleanup.")
    result: list[AgentNetwork] = []
    for network_id in dict.fromkeys(ids):
        inspected = _inspect_network(engine, network_id)
        labels = inspected.get("Labels")
        if not isinstance(labels, dict) or any(
            labels.get(key) != value for key, value in egress_uplink_network_labels(task, execution).items()
        ):
            raise NarrowError("Refusing to control an egress network with mismatched NARROW ownership labels.")
        name = inspected.get("Name")
        if inspected.get("Internal") is not False or inspected.get("Driver") != "bridge" or not isinstance(name, str) or not name.startswith("narrow-egress-uplink-"):
            raise NarrowError("Egress uplink network is not a verified non-internal bridge.")
        result.append(AgentNetwork(network_id, name, task, execution, "bridge"))
    return result


def cleanup_execution_egress_uplink_networks(
    engine: str,
    task_id: str,
    execution_id: str,
) -> int:
    if list_execution_egress_proxy_containers(engine, task_id, execution_id):
        raise NarrowError("Refusing to remove an egress uplink while its task/execution proxy sidecar exists.")
    networks = list_execution_egress_uplink_networks(engine, task_id, execution_id)
    removed = 0
    for network in networks:
        current = {item.network_id for item in list_execution_egress_uplink_networks(engine, task_id, execution_id)}
        if network.network_id not in current:
            raise NarrowError("Egress uplink ownership changed during cleanup.")
        _run(engine, ["network", "rm", network.network_id], timeout=30)
        removed += 1
    return removed


def cleanup_execution_agent_networks(
    engine: str,
    task_id: str,
    execution_id: str,
) -> int:
    """Remove the exact execution's internal networks only after its agents are gone."""
    if list_execution_agent_containers(engine, task_id, execution_id) or list_execution_egress_proxy_containers(engine, task_id, execution_id):
        raise NarrowError("Refusing to remove an agent network while task/execution agent containers still exist or an egress proxy remains.")
    networks = list_execution_agent_networks(engine, task_id, execution_id)
    removed = 0
    for network in networks:
        _owned_network(engine, network.network_id, task_id, execution_id)
        _run(engine, ["network", "rm", network.network_id], timeout=30)
        removed += 1
    return removed
