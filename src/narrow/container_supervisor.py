from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .container_lifecycle import agent_container_labels, cleanup_execution_agent_containers, list_execution_agent_networks
from .core import NarrowError, Store, task_workspace
from .policy_manifest import _validate_execution_id
from .agent_runtime import normalize_provider_argv, validate_agent_image
from .credential_broker import CredentialLease, validate_credential_lease
from .policy_manifest import read_policy_manifest

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_MAX_LINE = 1024 * 1024
_DEFAULT_OUTPUT_LIMIT = 64 * 1024


def _private_execution_dir(state_dir: Path, task_id: str, execution_id: str) -> Path:
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Invalid task ID for Docker supervisor.")
    execution = _validate_execution_id(execution_id)
    root = Path(state_dir)
    if root.name != ".narrow" or root.is_symlink() or not root.is_dir():
        raise NarrowError("Docker supervisor requires the real .narrow state directory.")
    current = root
    for component in ("runtime", "agent", task_id, execution):
        current = current / component
        try:
            current.mkdir(mode=0o700, exist_ok=True)
            if current.is_symlink() or not current.is_dir():
                raise NarrowError("Docker execution state contains a link or non-directory.")
            if os.name != "nt":
                os.chmod(current, 0o700)
        except NarrowError:
            raise
        except OSError as exc:
            raise NarrowError("Could not prepare private Docker execution state.") from exc
    return current


def _docker_environment() -> dict[str, str]:
    """Pass Docker connection settings, never provider or unrelated host secrets."""
    allowed = {
        "PATH", "HOME", "LANG", "LC_ALL",
        "DOCKER_CONFIG", "DOCKER_HOST",
    }
    return {
        key: value for key, value in os.environ.items()
        if key in allowed and isinstance(value, str) and "\x00" not in value
    }


def _stop_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise NarrowError("Docker client process did not terminate after forced shutdown.") from exc


def run_container_agent(
    command: list[str],
    *,
    store: Store,
    credential_lease: CredentialLease,
    task_id: str,
    execution_id: str,
    stdin_text: str,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    timeout_seconds: int = 3600,
    output_limit: int = _DEFAULT_OUTPUT_LIMIT,
) -> tuple[int, str]:
    """Run Docker in its own process group and always attempt exact-label cleanup."""
    if os.name == "nt":
        raise NarrowError("Docker agent process supervision is not enabled on Windows yet.")
    if not isinstance(command, list) or len(command) < 4:
        raise NarrowError("Docker supervisor requires a non-empty docker run argv.")
    if command[0].split("/")[-1] != "docker" or command[1] != "run":
        raise NarrowError("Docker supervisor accepts only an explicit docker run command.")
    if any(not isinstance(value, str) or not value or "\x00" in value for value in command):
        raise NarrowError("Docker supervisor argv contains an invalid argument.")
    if any(value == "--cidfile" for value in command):
        raise NarrowError("Docker cidfile is managed by NARROW and must not be supplied by callers.")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise NarrowError("Invalid task ID for Docker supervisor.")
    expected_execution = _validate_execution_id(execution_id)
    if not isinstance(store, Store) or store.state_dir.name != ".narrow":
        raise NarrowError("Docker supervisor requires the active NARROW task store.")
    store._check_state_paths()
    task = store.get_task(task_id)
    workspace = task_workspace(task)
    execution_dir = _private_execution_dir(store.state_dir, task_id, expected_execution)
    manifest_path = execution_dir / "policy.json"
    task_home = execution_dir / "home"
    if (
        not isinstance(credential_lease, CredentialLease)
        or credential_lease.provider != "codex"
        or credential_lease.task_id != task_id
        or credential_lease.execution_id != expected_execution
    ):
        raise NarrowError("Docker supervisor requires the exact Codex credential lease for this execution.")
    secret_path = validate_credential_lease(credential_lease)
    read_policy_manifest(manifest_path, expected_task_id=task_id, expected_execution_id=expected_execution)
    docker_host = os.environ.get("DOCKER_HOST")
    if docker_host and not docker_host.startswith("unix://"):
        raise NarrowError("The first Docker agent profile requires a local Unix-socket Docker daemon.")
    if os.environ.get("DOCKER_CONTEXT") not in (None, "", "default"):
        raise NarrowError("The first Docker agent profile refuses non-default or remote Docker contexts.")
    docker_context = subprocess.run(
        [command[0], "context", "show"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        env=_docker_environment(),
        shell=False,
        check=False,
    )
    if docker_context.returncode != 0 or docker_context.stdout.strip() != "default":
        raise NarrowError("The active Docker context is not the supported local default context.")
    expected_network = f"narrow-agent-{task_id[:32]}-{expected_execution.replace('-', '')[:12]}"
    if "--network" not in command or command[command.index("--network") + 1:command.index("--network") + 2] != [expected_network]:
        raise NarrowError("Docker agent command must use the exact task/execution internal network.")
    label_pairs = {command[index + 1] for index, value in enumerate(command[:-1]) if value == "--label"}
    required_labels = {f"{key}={value}" for key, value in agent_container_labels(task_id, expected_execution).items()}
    if not required_labels.issubset(label_pairs):
        raise NarrowError("Docker agent command is missing exact NARROW ownership labels.")
    if not {"--init", "--read-only"}.issubset(set(command)):
        raise NarrowError("Docker agent command must enable init and a read-only root filesystem.")
    if "--cap-drop" not in command or command[command.index("--cap-drop") + 1:command.index("--cap-drop") + 2] != ["ALL"]:
        raise NarrowError("Docker agent command must drop every Linux capability.")
    if "--security-opt" not in command or command[command.index("--security-opt") + 1:command.index("--security-opt") + 2] != ["no-new-privileges:true"]:
        raise NarrowError("Docker agent command must enable no-new-privileges.")

    if any(value in command for value in ("--privileged", "--volume", "-v", "--volumes-from", "--device", "--cap-add", "--env-file", "--pid", "--ipc", "--uts")):
        raise NarrowError("Docker agent command contains a forbidden host-access or privilege option.")
    mount_values = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--mount"
    ]
    mount_targets: dict[str, tuple[str, bool]] = {}
    for mount in mount_values:
        fields = dict(
            item.split("=", 1) for item in mount.split(",")
            if "=" in item
        )
        target = fields.get("target")
        source = fields.get("source")
        readonly = "readonly" in mount.split(",") or "ro" in mount.split(",")
        if fields.get("type") != "bind" or not target or not source or not source.startswith("/"):
            raise NarrowError("Docker agent bind mounts must use absolute host sources and explicit bind targets.")
        if target in mount_targets:
            raise NarrowError("Docker agent command repeats a bind mount target.")
        mount_targets[target] = (source, readonly)
    expected_mount_modes = {
        "/workspace": False,
        "/home/narrow": False,
        "/run/narrow/policy.json": True,
        "/run/secrets/provider-api-key": True,
    }
    if set(mount_targets) != set(expected_mount_modes):
        raise NarrowError("Docker agent must mount only its workspace, task home, policy manifest, and provider credential.")
    expected_sources = {
        "/workspace": workspace,
        "/home/narrow": task_home,
        "/run/narrow/policy.json": manifest_path,
        "/run/secrets/provider-api-key": secret_path,
    }
    for target, expected_source in expected_sources.items():
        try:
            info = expected_source.lstat()
            if expected_source.is_symlink():
                raise NarrowError("An expected agent mount source is a symbolic link.")
            if target in {"/workspace", "/home/narrow"}:
                if not expected_source.is_dir():
                    raise NarrowError("Agent workspace and task home mount sources must be directories.")
            elif not expected_source.is_file() or info.st_nlink != 1:
                raise NarrowError("Agent policy and credential mount sources must be regular unlinked files.")
            if Path(mount_targets[target][0]).resolve(strict=True) != expected_source.resolve(strict=True):
                raise NarrowError("A Docker bind mount does not point to the exact task-scoped source.")
        except NarrowError:
            raise
        except OSError as exc:
            raise NarrowError("An expected task-scoped Docker mount source is unavailable.") from exc
    for target, readonly in expected_mount_modes.items():
        if mount_targets[target][1] is not readonly:
            raise NarrowError("Docker policy and credential mounts must be read-only; workspace and task home must be writable.")
    allowed_env = {
        "HOME", "PATH", "LANG", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "NO_PROXY", "no_proxy", "NARROW_POLICY_MANIFEST", "NARROW_TASK_ID",
        "NARROW_EXECUTION_ID", "NARROW_CONTAINER_EVENT_SPOOL",
    }
    env_items = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--env"
    ]
    if any("=" not in item for item in env_items):
        raise NarrowError("Docker agent environment entries must use explicit KEY=value pairs.")
    env_values = dict(item.split("=", 1) for item in env_items)
    if any(name not in allowed_env for name in env_values) or len(env_items) != len(env_values):
        raise NarrowError("Docker agent environment contains an unapproved or duplicate variable.")
    expected_env = {
        "HOME": "/home/narrow",
        "NARROW_POLICY_MANIFEST": "/run/narrow/policy.json",
        "NARROW_TASK_ID": task_id,
        "NARROW_EXECUTION_ID": expected_execution,
        "NARROW_CONTAINER_EVENT_SPOOL": "/home/narrow/narrow-events.jsonl",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }
    if any(env_values.get(key) != value for key, value in expected_env.items()):
        raise NarrowError("Docker agent environment does not match the task-scoped runtime contract.")
    proxy = env_values.get("HTTP_PROXY", "")
    if (
        proxy != env_values.get("HTTPS_PROXY")
        or proxy != env_values.get("http_proxy")
        or proxy != env_values.get("https_proxy")
        or not re.fullmatch(r"http://narrow:[A-Za-z0-9_-]{32,128}@narrow-egress-proxy:3128", proxy)
    ):
        raise NarrowError("Docker agent must use the authenticated per-execution NARROW egress proxy.")
    if "--entrypoint" not in command or command[command.index("--entrypoint") + 1:command.index("--entrypoint") + 2] != ["python"]:
        raise NarrowError("Docker agent must start through the NARROW in-container entrypoint.")
    entrypoint_index = command.index("--entrypoint")
    if entrypoint_index + 10 >= len(command):
        raise NarrowError("Docker agent image or in-container entrypoint arguments are incomplete.")
    validate_agent_image(command[entrypoint_index + 2])
    if command[entrypoint_index + 3:entrypoint_index + 10] != [
        "-m", "narrow.agent_entrypoint", "--provider", "codex",
        "--credential-file", "/run/secrets/provider-api-key", "--",
    ]:
        raise NarrowError("Docker agent must invoke the constrained Codex credential entrypoint.")
    normalize_provider_argv("codex", command[entrypoint_index + 10:])
    if "--workdir" not in command or command[command.index("--workdir") + 1:command.index("--workdir") + 2] != ["/workspace"]:
        raise NarrowError("Docker agent working directory must be the virtual /workspace.")
    if "--user" not in command or not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", command[command.index("--user") + 1]):
        raise NarrowError("Docker agent must run as an explicit non-root user and group.")
    if "--rm" not in command or "--interactive" not in command:
        raise NarrowError("Docker agent must use automatic container removal and stdin forwarding.")
    if "--tmpfs" not in command or command[command.index("--tmpfs") + 1:command.index("--tmpfs") + 2] != ["/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777"]:
        raise NarrowError("Docker agent must use the bounded no-exec private tmpfs profile.")
    networks = list_execution_agent_networks("docker", task_id, expected_execution)
    if len(networks) != 1 or networks[0].name != expected_network:
        raise NarrowError("The exact task/execution internal Docker network is not present and verified.")
    if not isinstance(stdin_text, str) or "\x00" in stdin_text:
        raise NarrowError("Docker agent prompt must be text without NUL characters.")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 24 * 60 * 60:
        raise NarrowError("Docker agent timeout must be from 1 second to 24 hours.")
    if not isinstance(output_limit, int) or isinstance(output_limit, bool) or not 1024 <= output_limit <= 1024 * 1024:
        raise NarrowError("Docker agent output limit must be from 1 KiB to 1 MiB.")

    execution_dir = _private_execution_dir(store.state_dir, task_id, expected_execution)
    cidfile = execution_dir / "container.cid"
    if cidfile.exists() or cidfile.is_symlink():
        raise NarrowError("A Docker cidfile already exists for this execution; refusing to overwrite it.")
    launch = [command[0], command[1], "--cidfile", str(cidfile), *command[2:]]
    captured = bytearray()
    capture_lock = threading.Lock()
    callback_errors: list[BaseException] = []
    reader_errors: list[str] = []
    pending = bytearray()
    oversized_line = False

    def retain(data: bytes) -> None:
        with capture_lock:
            captured.extend(data)
            if len(captured) > output_limit:
                del captured[:len(captured) - output_limit]

    def consume_line(line: bytes) -> None:
        nonlocal oversized_line
        if oversized_line:
            oversized_line = False
            retain(b"\n[provider event line exceeded 1 MiB and was discarded]\n")
            return
        stripped = line.strip()
        if not stripped:
            return
        try:
            value = json.loads(stripped.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            retain(stripped[-4096:] + b"\n")
            return
        if isinstance(value, dict) and event_callback is not None:
            try:
                event_callback(value)
            except BaseException as exc:
                callback_errors.append(exc)
        elif not isinstance(value, dict):
            retain(b"\n[non-object provider output discarded]\n")

    def drain(stream: Any) -> None:
        nonlocal oversized_line
        try:
            while True:
                reader = getattr(stream, "read1", stream.read)
                chunk = reader(8192)
                if not chunk:
                    break
                pending.extend(chunk)
                while True:
                    marker = pending.find(b"\n")
                    if marker < 0:
                        if len(pending) > _MAX_LINE:
                            pending.clear()
                            oversized_line = True
                        break
                    line = bytes(pending[:marker])
                    del pending[:marker + 1]
                    if len(line) > _MAX_LINE:
                        retain(b"\n[provider event line exceeded 1 MiB and was discarded]\n")
                    else:
                        consume_line(line)
            if pending and not oversized_line:
                consume_line(bytes(pending))
        except (OSError, ValueError) as exc:
            reader_errors.append(type(exc).__name__)

    process: subprocess.Popen[bytes] | None = None
    timed_out = False
    interrupted = False
    try:
        process = subprocess.Popen(
            launch,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=execution_dir,
            env=_docker_environment(),
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        assert process.stdin is not None and process.stdout is not None
        reader = threading.Thread(target=drain, args=(process.stdout,), name="narrow-docker-agent-output", daemon=True)
        reader.start()
        try:
            payload = stdin_text.encode("utf-8")
            if len(payload) > 4 * 1024 * 1024:
                raise NarrowError("Docker agent prompt exceeds NARROW's 4 MiB safety limit.")
            process.stdin.write(payload)
            process.stdin.close()
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if callback_errors:
                    _stop_group(process)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _stop_group(process)
                    break
                time.sleep(min(0.1, remaining))
            exit_code = process.wait(timeout=5)
        except KeyboardInterrupt:
            interrupted = True
            _stop_group(process)
            raise
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            reader.join(timeout=5)
            if process.stdout:
                process.stdout.close()

        if callback_errors:
            raise NarrowError("Host provider event callback failed; the container was stopped and cleaned up.")
        if reader_errors:
            retain(("\n[provider output reader warning: " + ",".join(reader_errors[:3]) + "]\n").encode())
        if timed_out:
            retain((f"\n[Docker agent timed out after {timeout_seconds} seconds.]\n").encode())
            return 124, bytes(captured).decode("utf-8", errors="replace")
        return exit_code, bytes(captured).decode("utf-8", errors="replace")
    finally:
        if process is not None and process.poll() is None:
            _stop_group(process)
        cleanup_execution_agent_containers("docker", task_id, execution_id)
        if cidfile.exists() or cidfile.is_symlink():
            try:
                info = cidfile.lstat()
                if cidfile.is_symlink() or not cidfile.is_file() or info.st_nlink != 1 or info.st_size > 128:
                    raise NarrowError("Docker cidfile failed NARROW's post-run file integrity check.")
                raw_id = cidfile.read_text(encoding="ascii").strip()
                if raw_id and not _CONTAINER_ID.fullmatch(raw_id):
                    raise NarrowError("Docker returned a malformed container ID; the record was retained for manual inspection.")
                cidfile.unlink()
            except OSError as exc:
                raise NarrowError("Could not safely remove the private Docker cidfile.") from exc
        if interrupted:
            pass
