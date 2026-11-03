from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from .core import NarrowError
from .container_lifecycle import agent_container_label_args
from .credential_broker import CredentialLease, validate_credential_lease
from .policy_manifest import _validate_execution_id, read_policy_manifest

_IMAGE_DIGEST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}@sha256:[0-9a-f]{64}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MODEL_EFFORT = re.compile(r'^model_reasoning_effort="(?:low|medium|high|xhigh)"$')


def validate_agent_image(image: str) -> str:
    """Require an immutable OCI digest reference; mutable tags are refused."""
    if not isinstance(image, str) or not _IMAGE_DIGEST.fullmatch(image):
        raise NarrowError("Agent image must be pinned by an immutable @sha256:<64 hex> digest; mutable tags are refused.")
    return image


def normalize_provider_argv(provider: str, command: list[str]) -> list[str]:
    """Rebuild the known Codex argv and replace the host path with /workspace."""
    if provider != "codex":
        raise NarrowError("The first container launch profile supports Codex only; Claude needs a container-specific settings and hook profile.")
    if (
        not isinstance(command, list)
        or any(not isinstance(item, str) or not item or "\x00" in item for item in command)
        or len(command) < 9
        or command[:2] != ["codex", "exec"]
        or command[-1] != "-"
    ):
        raise NarrowError("Container provider command must match the NARROW Codex exec adapter contract.")
    tokens = command[2:-1]
    cursor = 0
    result = ["codex", "exec"]
    if tokens and tokens[0] == "resume":
        if len(tokens) < 2 or not _SESSION_ID.fullmatch(tokens[1]):
            raise NarrowError("Codex resume session ID is invalid for container execution.")
        result.extend(["resume", tokens[1]])
        cursor = 2
    if tokens[cursor:cursor + 1] != ["--json"]:
        raise NarrowError("Codex container command must enable structured JSONL output.")
    result.append("--json")
    cursor += 1
    if tokens[cursor:cursor + 1] != ["--cd"] or cursor + 1 >= len(tokens):
        raise NarrowError("Codex container command must contain one workspace argument.")
    host_workspace = tokens[cursor + 1]
    if not host_workspace or host_workspace.startswith("--"):
        raise NarrowError("Codex host workspace argument is malformed.")
    result.extend(["--cd", "/workspace"])
    cursor += 2
    if tokens[cursor:cursor + 2] != ["--sandbox", "workspace-write"]:
        raise NarrowError("Codex container command must retain its workspace-write sandbox.")
    result.extend(["--sandbox", "workspace-write"])
    cursor += 2
    if tokens[cursor:cursor + 2] != ["--ask-for-approval", "never"]:
        raise NarrowError("Codex container command must retain its non-interactive approval policy.")
    result.extend(["--ask-for-approval", "never"])
    cursor += 2
    if tokens[cursor:cursor + 1] == ["--model"]:
        if cursor + 1 >= len(tokens):
            raise NarrowError("Codex model option is missing its value.")
        model = tokens[cursor + 1]
        if len(model) > 120 or model.startswith("-") or "/" in model or "\\" in model:
            raise NarrowError("Codex model value is invalid for container execution.")
        result.extend(["--model", model])
        cursor += 2
    if tokens[cursor:cursor + 1] == ["--config"]:
        if cursor + 1 >= len(tokens) or not _MODEL_EFFORT.fullmatch(tokens[cursor + 1]):
            raise NarrowError("Container Codex configuration may set only the validated reasoning-effort enum.")
        result.extend(["--config", tokens[cursor + 1]])
        cursor += 2
    if cursor != len(tokens):
        raise NarrowError("Codex container command contains an unsupported or unsafe argument.")
    result.append("-")
    return result


def _validated_mount_source(value: Path | str, *, directory: bool) -> str:
    """Return one real absolute POSIX bind source without Docker CSV ambiguity."""
    source = Path(value)
    try:
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise NarrowError("Container bind mount source must not be a symbolic link.")
        if directory and not stat.S_ISDIR(metadata.st_mode):
            raise NarrowError("Container directory mount source is not a real directory.")
        if not directory and (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
        ):
            raise NarrowError("Container file mount source must be a regular unlinked file.")
        resolved = source.resolve(strict=True)
    except NarrowError:
        raise
    except OSError as exc:
        raise NarrowError("Container bind mount source is unavailable.") from exc
    rendered = str(resolved)
    if (
        not resolved.is_absolute()
        or "," in rendered
        or "\x00" in rendered
        or "\n" in rendered
        or "\r" in rendered
    ):
        raise NarrowError("Container bind mount source is not safe for Docker's mount syntax.")
    return rendered


def build_docker_agent_command(
    image: str,
    provider_command: list[str],
    *,
    workspace_path: Path | str,
    task_home_path: Path | str,
    policy_manifest_path: Path | str,
    credential_lease: CredentialLease,
    task_id: str,
    execution_id: str,
    proxy_token: str,
    uid: int | None = None,
    gid: int | None = None,
) -> list[str]:
    """Build the exact, restricted Docker argv accepted by the host supervisor.

    The internal network and authenticated egress sidecar must already exist.
    This function only plans the agent process; it does not create or start
    Docker resources and must not be described as end-to-end egress enforcement.
    """
    pinned_image = validate_agent_image(image)
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
        raise NarrowError("Invalid task ID for Docker agent command.")
    execution = _validate_execution_id(execution_id)
    if (
        not isinstance(credential_lease, CredentialLease)
        or credential_lease.provider != "codex"
        or credential_lease.task_id != task_id
        or credential_lease.execution_id != execution
    ):
        raise NarrowError("Docker agent command requires the exact Codex credential lease for this execution.")
    secret_path = validate_credential_lease(credential_lease)
    workspace = _validated_mount_source(workspace_path, directory=True)
    task_home = _validated_mount_source(task_home_path, directory=True)
    manifest = _validated_mount_source(policy_manifest_path, directory=False)
    read_policy_manifest(
        Path(manifest),
        expected_task_id=task_id,
        expected_execution_id=execution,
    )
    secret = _validated_mount_source(secret_path, directory=False)
    if os.name != "nt":
        if stat.S_IMODE(Path(secret).stat().st_mode) != 0o400:
            raise NarrowError("Docker provider credential mount must have owner-read-only mode 0400.")
    if (
        not isinstance(proxy_token, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", proxy_token)
    ):
        raise NarrowError("Docker agent requires a bounded URL-safe per-execution egress capability token.")
    if uid is None or gid is None:
        if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
            raise NarrowError("Docker agent requires an explicit non-root POSIX UID and GID.")
        uid = os.getuid() if uid is None else uid
        gid = os.getgid() if gid is None else gid
    if (
        not isinstance(uid, int) or isinstance(uid, bool)
        or not isinstance(gid, int) or isinstance(gid, bool)
        or not 1 <= uid <= 65535 or not 1 <= gid <= 65535
    ):
        raise NarrowError("Docker agent UID and GID must be explicit non-root numeric identities.")

    normalized_provider = normalize_provider_argv("codex", provider_command)
    network = f"narrow-agent-{task_id[:32]}-{execution.replace('-', '')[:12]}"
    container_name = f"narrow-agent-{task_id[:24]}-{execution.replace('-', '')[:12]}"
    proxy_url = f"http://narrow:{proxy_token}@narrow-egress-proxy:3128"
    spool = "/home/narrow/.local/state/narrow/hook-events.jsonl"

    command = [
        "docker", "run",
        "--rm",
        "--init",
        "--interactive",
        "--read-only",
        "--name", container_name,
        "--network", network,
        "--workdir", "/workspace",
        "--user", f"{uid}:{gid}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--pids-limit", "256",
        "--memory", "2g",
        "--cpus", "2.0",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777",
        "--mount", f"type=bind,source={workspace},target=/workspace",
        "--mount", f"type=bind,source={task_home},target=/home/narrow",
        "--mount", f"type=bind,source={manifest},target=/run/narrow/policy.json,readonly",
        "--mount", f"type=bind,source={secret},target=/run/secrets/provider-api-key,readonly",
    ]
    command.extend(agent_container_label_args(task_id, execution))
    for key, value in (
        ("HOME", "/home/narrow"),
        ("LANG", "C.UTF-8"),
        ("HTTP_PROXY", proxy_url),
        ("HTTPS_PROXY", proxy_url),
        ("http_proxy", proxy_url),
        ("https_proxy", proxy_url),
        ("NO_PROXY", "localhost,127.0.0.1"),
        ("no_proxy", "localhost,127.0.0.1"),
        ("NARROW_POLICY_MANIFEST", "/run/narrow/policy.json"),
        ("NARROW_TASK_ID", task_id),
        ("NARROW_EXECUTION_ID", execution),
        ("NARROW_CONTAINER_EVENT_SPOOL", spool),
    ):
        command.extend(["--env", f"{key}={value}"])
    command.extend([
        "--entrypoint", "python",
        pinned_image,
        "-m", "narrow.agent_entrypoint",
        "--provider", "codex",
        "--credential-file", "/run/secrets/provider-api-key",
        "--",
        *normalized_provider,
    ])
    return command
