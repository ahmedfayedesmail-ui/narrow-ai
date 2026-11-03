"""Execution-scoped Docker egress-proxy sidecar provisioning.

The agent joins only an internal network. The proxy joins that internal network
and one execution-specific routed uplink. No host port is published. The host
supervisor remains authoritative; Docker daemon administrators are trusted.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_runtime import validate_agent_image
from .container_lifecycle import (
    AgentNetwork,
    cleanup_execution_agent_networks,
    cleanup_execution_egress_proxy_containers,
    cleanup_execution_egress_uplink_networks,
    create_execution_agent_network,
    create_execution_egress_uplink_network,
    egress_proxy_container_label_args,
    egress_proxy_container_labels,
    list_execution_agent_containers,
    list_execution_agent_networks,
    list_execution_egress_proxy_containers,
    list_execution_egress_uplink_networks,
)
from .core import NarrowError, Store
from .egress_proxy import normalize_allowlist
from .policy_manifest import _validate_execution_id

_TASK_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_CONTAINER_ID_CHARS = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class EgressProxyRuntime:
    """Host reference to a ready, execution-owned proxy sidecar."""

    container_id: str
    container_name: str
    token: str
    token_path: Path
    agent_network: AgentNetwork
    uplink_network: AgentNetwork
    image: str
    allowed_hosts: tuple[str, ...]


def _execution_root(store: Store, task_id: str, execution_id: str) -> Path:
    if not isinstance(store, Store) or store.state_dir.name != ".narrow":
        raise NarrowError("Egress sidecar requires the active NARROW task store.")
    if not isinstance(task_id, str) or not task_id or len(task_id) > 64 or any(ch not in _TASK_ID_CHARS for ch in task_id):
        raise NarrowError("Invalid task ID for egress sidecar.")
    execution = _validate_execution_id(execution_id)
    store._check_state_paths()
    store.get_task(task_id)
    root = store.state_dir
    for part in ("runtime", "agent", task_id, execution):
        root = root / part
        try:
            root.mkdir(mode=0o700, exist_ok=True)
            metadata = root.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise NarrowError("Egress sidecar state directory contains a link or non-directory.")
            if os.name != "nt":
                os.chmod(root, 0o700)
        except NarrowError:
            raise
        except OSError as exc:
            raise NarrowError("Could not prepare private egress sidecar state.") from exc
    return root


def _token_file(root: Path) -> tuple[str, Path]:
    token = secrets.token_urlsafe(48)
    if len(token) < 32 or len(token) > 128:
        raise NarrowError("Generated egress capability token has an unexpected length.")
    path = root / "proxy.token"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o400)
        payload = (token + "\n").encode("ascii")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_size != len(payload)
        ):
            raise NarrowError("Could not verify the private egress capability file.")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return token, path


def remove_execution_proxy_token_file(store: Store, task_id: str, execution_id: str) -> bool:
    """Remove only one validated 0400 proxy token file for an exact execution."""
    if os.name == "nt":
        raise NarrowError("Proxy capability cleanup is disabled on Windows.")
    if not isinstance(store, Store) or store.state_dir.name != ".narrow":
        raise NarrowError("Proxy capability cleanup requires the active NARROW task store.")
    if not isinstance(task_id, str) or not task_id or len(task_id) > 64 or any(ch not in _TASK_ID_CHARS for ch in task_id):
        raise NarrowError("Invalid task ID for proxy capability cleanup.")
    execution = _validate_execution_id(execution_id)
    root = store.state_dir / "runtime" / "agent" / task_id / execution
    path = root / "proxy.token"
    try:
        for directory in (
            store.state_dir,
            store.state_dir / "runtime",
            store.state_dir / "runtime" / "agent",
            store.state_dir / "runtime" / "agent" / task_id,
            root,
        ):
            metadata = directory.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise NarrowError("Proxy capability path contains a link or non-directory.")
        store._check_state_paths()
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_size > 256
            or metadata.st_uid != os.getuid()
        ):
            raise NarrowError("Refusing to remove an unsafe proxy capability file.")
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError("Could not safely remove the proxy capability file.") from exc


def _docker_environment() -> dict[str, str]:
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "DOCKER_CONFIG", "DOCKER_HOST"}
    result = {
        key: value for key, value in os.environ.items()
        if key in allowed and isinstance(value, str) and "\x00" not in value
    }
    docker_host = result.get("DOCKER_HOST")
    if docker_host and not docker_host.startswith("unix://"):
        raise NarrowError("Egress proxy provisioning requires a local Unix-socket Docker daemon.")
    if os.environ.get("DOCKER_CONTEXT") not in (None, "", "default"):
        raise NarrowError("Egress proxy provisioning refuses non-default or remote Docker contexts.")
    return result


def _docker(args: list[str], *, timeout: float = 20.0) -> str:
    executable = shutil.which("docker")
    if not executable:
        raise NarrowError("Docker was not found; egress proxy provisioning failed closed.")
    try:
        result = subprocess.run(
            [executable, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_docker_environment(),
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NarrowError(f"Docker egress operation failed: {type(exc).__name__}.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Docker command failed").strip()
        raise NarrowError("Docker egress operation failed: " + detail[:800])
    return result.stdout.strip()


def _inspect_container(container_id: str) -> dict[str, Any]:
    if (
        not isinstance(container_id, str)
        or len(container_id) < 12
        or len(container_id) > 64
        or any(ch not in _CONTAINER_ID_CHARS for ch in container_id)
    ):
        raise NarrowError("Docker returned an invalid egress sidecar ID.")
    try:
        payload = json.loads(_docker(["container", "inspect", container_id]))
    except json.JSONDecodeError as exc:
        raise NarrowError("Docker returned malformed egress sidecar inspect JSON.") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise NarrowError("Docker returned an unexpected egress sidecar inspect response.")
    return payload[0]


def _verify_sidecar(
    inspected: dict[str, Any],
    *,
    task_id: str,
    execution_id: str,
    container_name: str,
    image: str,
    token_path: Path,
    agent_network: AgentNetwork,
    uplink_network: AgentNetwork,
) -> str:
    config = inspected.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    expected_labels = egress_proxy_container_labels(task_id, execution_id)
    if not isinstance(labels, dict) or any(labels.get(key) != value for key, value in expected_labels.items()):
        raise NarrowError("Egress proxy sidecar does not carry exact NARROW execution ownership labels.")
    if not isinstance(config, dict) or config.get("Image") != image:
        raise NarrowError("Egress proxy sidecar image differs from the requested digest-pinned image.")
    state = inspected.get("State")
    if not isinstance(state, dict) or state.get("Running") is not True:
        raise NarrowError("Egress proxy sidecar is not running.")
    host = inspected.get("HostConfig")
    if not isinstance(host, dict):
        raise NarrowError("Egress proxy sidecar is missing Docker host configuration.")
    if (
        host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or host.get("CapAdd") not in (None, [])
        or "ALL" not in (host.get("CapDrop") or [])
        or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])
        or host.get("PortBindings") not in (None, {})
        or host.get("NetworkMode") != agent_network.name
    ):
        raise NarrowError("Egress proxy sidecar violates NARROW's no-publish, read-only, least-privilege profile.")
    mounts = inspected.get("Mounts")
    if (
        not isinstance(mounts, list)
        or len(mounts) != 1
        or not isinstance(mounts[0], dict)
        or mounts[0].get("Destination") != "/run/secrets/proxy-token"
        or mounts[0].get("RW") is not False
        or Path(str(mounts[0].get("Source", ""))).resolve() != token_path.resolve()
    ):
        raise NarrowError("Egress proxy sidecar must receive only the exact read-only capability-file mount.")
    network_settings = inspected.get("NetworkSettings")
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    if not isinstance(networks, dict) or set(networks) != {agent_network.name, uplink_network.name}:
        raise NarrowError("Egress proxy sidecar must attach to exactly its internal agent network and dedicated uplink.")
    internal = networks[agent_network.name]
    aliases = internal.get("Aliases", []) if isinstance(internal, dict) else []
    if not isinstance(aliases, list) or "narrow-egress-proxy" not in aliases:
        raise NarrowError("Egress proxy sidecar is missing its agent-network DNS alias.")
    name = inspected.get("Name")
    if name != "/" + container_name:
        raise NarrowError("Egress proxy sidecar name does not match the execution-scoped name.")
    return str(inspected.get("Id", ""))


def provision_execution_egress_proxy(
    store: Store,
    task_id: str,
    execution_id: str,
    image: str,
    allowed_hosts: list[str] | tuple[str, ...],
) -> EgressProxyRuntime:
    """Create a proxy-only dual-homed sidecar, verify it, and wait for readiness.

    The agent itself must be launched only on the returned internal network.
    This function never publishes a host port and refuses to adopt unrelated
    containers or networks. The proxy image must include narrow.egress_proxy.
    """
    if os.name == "nt" or not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        raise NarrowError("The first egress sidecar profile is supported only on POSIX hosts.")
    uid, gid = os.getuid(), os.getgid()
    if uid == 0 or gid == 0:
        raise NarrowError("The first egress sidecar profile refuses root-owned host execution.")
    pinned_image = validate_agent_image(image)
    allowed = tuple(sorted(normalize_allowlist(allowed_hosts)))
    if _docker(["context", "show"], timeout=5) != "default":
        raise NarrowError("Egress sidecar provisioning requires the local default Docker context.")
    root = _execution_root(store, task_id, execution_id)
    if list_execution_egress_proxy_containers("docker", task_id, execution_id):
        raise NarrowError("An egress proxy sidecar already exists for this execution; refusing to replace it.")
    agent_networks = list_execution_agent_networks("docker", task_id, execution_id)
    if len(agent_networks) > 1:
        raise NarrowError("Multiple internal agent networks exist for this execution.")
    uplinks = list_execution_egress_uplink_networks("docker", task_id, execution_id)
    if len(uplinks) > 1:
        raise NarrowError("Multiple egress uplink networks exist for this execution.")
    agent_network = agent_networks[0] if agent_networks else create_execution_agent_network("docker", task_id, execution_id)
    uplink_network = uplinks[0] if uplinks else create_execution_egress_uplink_network("docker", task_id, execution_id)
    token, token_path = _token_file(root)
    container_name = f"narrow-egress-proxy-{task_id[:20]}-{_validate_execution_id(execution_id).replace('-', '')[:12]}"
    created_id = ""
    try:
        command = [
            "run", "--detach", "--rm",
            "--name", container_name,
            "--network", agent_network.name,
            "--network-alias", "narrow-egress-proxy",
            "--user", f"{uid}:{gid}",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", "64",
            "--memory", "256m",
            "--cpus", "0.5",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
            "--mount", f"type=bind,source={token_path.resolve(strict=True)},target=/run/secrets/proxy-token,readonly",
            *egress_proxy_container_label_args(task_id, execution_id),
            "--entrypoint", "python",
            pinned_image,
            "-m", "narrow.egress_proxy",
            "--bind", "0.0.0.0",
            "--container-private-listener",
            "--port", "3128",
            "--token-file", "/run/secrets/proxy-token",
        ]
        for host in allowed:
            command.extend(["--allow-host", host])
        created_id = _docker(command, timeout=60)
        if (
            len(created_id) < 12
            or len(created_id) > 64
            or any(ch not in _CONTAINER_ID_CHARS for ch in created_id)
        ):
            raise NarrowError("Docker did not return a valid egress sidecar container ID.")
        _docker(["network", "connect", "--alias", "narrow-egress-uplink", uplink_network.name, created_id], timeout=30)
        inspected = _inspect_container(created_id)
        verified_id = _verify_sidecar(
            inspected,
            task_id=task_id,
            execution_id=execution_id,
            container_name=container_name,
            image=pinned_image,
            token_path=token_path,
            agent_network=agent_network,
            uplink_network=uplink_network,
        )
        if verified_id != created_id:
            raise NarrowError("Egress proxy sidecar identity changed during verification.")
        ready = False
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            try:
                _docker([
                    "exec", created_id, "python", "-c",
                    "import socket; s=socket.create_connection(('127.0.0.1',3128),timeout=1); s.close()",
                ], timeout=3)
                ready = True
                break
            except NarrowError:
                time.sleep(0.2)
        if not ready:
            raise NarrowError("Egress proxy sidecar did not become ready within 12 seconds.")
        return EgressProxyRuntime(
            container_id=created_id,
            container_name=container_name,
            token=token,
            token_path=token_path,
            agent_network=agent_network,
            uplink_network=uplink_network,
            image=pinned_image,
            allowed_hosts=allowed,
        )
    except Exception:
        try:
            cleanup_execution_egress_proxy_containers("docker", task_id, execution_id)
            if created_id:
                _docker(["container", "rm", "--force", created_id], timeout=20)
        except Exception:
            pass
        try:
            if not list_execution_egress_proxy_containers("docker", task_id, execution_id):
                cleanup_execution_egress_uplink_networks("docker", task_id, execution_id)
                cleanup_execution_agent_networks("docker", task_id, execution_id)
        except Exception:
            pass
        try:
            remove_execution_proxy_token_file(store, task_id, execution_id)
        except Exception:
            pass
        raise


def run_codex_container_agent(
    store: Store,
    task_id: str,
    execution_id: str,
    provider_command: list[str],
    *,
    agent_image: str,
    proxy_image: str,
    allowed_hosts: list[str] | tuple[str, ...],
    stdin_text: str,
    event_callback: Any = None,
    timeout_seconds: int = 3600,
    output_limit: int = 64 * 1024,
) -> tuple[int, str]:
    """Run one Codex agent through the internal-network-only proxy topology.

    The provider API key must be explicitly present as OPENAI_API_KEY in the
    host environment. The Codex image must contain the CLI and the NARROW
    in-container entrypoint; the proxy image must contain narrow.egress_proxy.
    This is POSIX/local-Docker only and remains opt-in.
    """
    from .agent_runtime import build_docker_agent_command
    from .container_supervisor import run_container_agent
    from .credential_broker import issue_credential_lease, revoke_credential_lease
    from .policy_manifest import write_policy_manifest
    from .core import task_workspace

    if os.name == "nt" or not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        raise NarrowError("The first container agent profile is available only on POSIX hosts.")
    if not isinstance(stdin_text, str) or "\x00" in stdin_text or len(stdin_text.encode("utf-8")) > 2 * 1024 * 1024:
        raise NarrowError("Container agent prompt must be bounded UTF-8 text.")
    if not isinstance(provider_command, list) or not provider_command:
        raise NarrowError("Container agent requires the reviewed Codex provider command.")
    if (
        not isinstance(task_id, str)
        or not task_id
        or len(task_id) > 64
        or any(character not in _TASK_ID_CHARS for character in task_id)
    ):
        raise NarrowError("Invalid task ID for container agent execution.")
    execution = _validate_execution_id(execution_id)
    validate_agent_image(agent_image)
    validate_agent_image(proxy_image)

    task = store.get_task(task_id)
    execution_root = _execution_root(store, task_id, execution)
    task_home = execution_root / "home"
    try:
        task_home.mkdir(mode=0o700, exist_ok=True)
        metadata = task_home.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise NarrowError("Container task home is not a real directory.")
        os.chmod(task_home, 0o700)
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError("Could not prepare private container task home.") from exc

    manifest = write_policy_manifest(store, task_id, execution)
    lease = issue_credential_lease(store, "codex", task_id, execution)
    supervisor_started = False
    proxy_runtime: EgressProxyRuntime | None = None
    try:
        proxy_runtime = provision_execution_egress_proxy(
            store, task_id, execution, proxy_image, allowed_hosts
        )
        command = build_docker_agent_command(
            agent_image,
            provider_command,
            workspace_path=task_workspace(task),
            task_home_path=task_home,
            policy_manifest_path=manifest.path,
            credential_lease=lease,
            task_id=task_id,
            execution_id=execution,
            proxy_token=proxy_runtime.token,
            uid=os.getuid(),
            gid=os.getgid(),
        )
        store.add_event(task_id, "CONTAINER_AGENT_LAUNCH_AUTHORIZED", {
            "execution_id": execution,
            "provider": "codex",
            "agent_image": agent_image,
            "proxy_image": proxy_image,
            "allowed_hosts": list(proxy_runtime.allowed_hosts),
            "network_model": "internal-agent-network-plus-proxy-only-uplink",
        })
        supervisor_started = True
        return run_container_agent(
            command,
            store=store,
            credential_lease=lease,
            task_id=task_id,
            execution_id=execution,
            stdin_text=stdin_text,
            event_callback=event_callback,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
        )
    finally:
        if not supervisor_started:
            try:
                revoke_credential_lease(lease)
            finally:
                try:
                    cleanup_execution_egress_proxy_containers("docker", task_id, execution)
                    if not list_execution_egress_proxy_containers("docker", task_id, execution):
                        cleanup_execution_egress_uplink_networks("docker", task_id, execution)
                        cleanup_execution_agent_networks("docker", task_id, execution)
                        remove_execution_proxy_token_file(store, task_id, execution)
                except Exception:
                    # Preserve the original launch error. Exact recovery remains
                    # available through narrow recover-agent for this execution.
                    pass
