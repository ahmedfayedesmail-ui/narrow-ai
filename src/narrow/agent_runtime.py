from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from .core import NarrowError
from .container_lifecycle import agent_container_label_args
from .credential_broker import CredentialLease, validate_credential_lease
from .policy_manifest import _validate_execution_id, read_policy_manifest
from .egress_proxy import read_token_file

_IMAGE_DIGEST = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?::[0-9]{1,5})?(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*@sha256:[0-9a-f]{64}$"
)
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


def validate_claude_container_settings(
    settings_path: Path | str,
    mcp_path: Path | str,
) -> tuple[Path, Path]:
    """Verify the exact manifest-hook settings profile allowed inside Docker."""
    settings_file = Path(settings_path)
    mcp_file = Path(mcp_path)
    for item in (settings_file, mcp_file):
        try:
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise NarrowError("Claude container settings must be regular, unlinked host files.")
        except OSError as exc:
            raise NarrowError("Claude container settings are missing or unavailable.") from exc
    try:
        settings_data = json.loads(settings_file.read_text(encoding="utf-8"))
        mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NarrowError("Claude container settings are not valid task-local JSON.") from exc
    if not isinstance(settings_data, dict) or "sandbox" in settings_data:
        raise NarrowError("Claude container settings must delegate containment to the outer Docker boundary.")
    permissions = settings_data.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("blockReadsOutsideWorkingDirectories") is not True:
        raise NarrowError("Claude container settings must retain the working-directory read restriction.")
    hooks = settings_data.get("hooks")
    if not isinstance(hooks, dict) or "PreToolUse" not in hooks:
        raise NarrowError("Claude container settings must include the NARROW direct-file PreToolUse gate.")
    allowed_events = {
        "session-start", "pre-tool-use", "post-tool-use", "post-tool-use-failure",
        "pre-compact", "stop", "session-end", "subagent-start", "subagent-stop",
        "task-created", "task-completed",
    }
    for hook_groups in hooks.values():
        if not isinstance(hook_groups, list):
            raise NarrowError("Claude container hook settings are malformed.")
        for group in hook_groups:
            hook_list = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(hook_list, list):
                raise NarrowError("Claude container hook settings are malformed.")
            for hook in hook_list:
                command = hook.get("command") if isinstance(hook, dict) else None
                if not isinstance(command, str) or not command.startswith("python -m narrow hook claude "):
                    raise NarrowError("Claude container hooks must invoke the in-image manifest-only hook CLI.")
                event = command.removeprefix("python -m narrow hook claude ")
                if event not in allowed_events:
                    raise NarrowError("Claude container hook command contains an unsupported event.")
    if mcp_data != {"mcpServers": {}}:
        raise NarrowError("Claude container MCP configuration must be exactly empty.")
    return settings_file, mcp_file


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
    proxy_token_path: Path | str,
    uid: int | None = None,
    gid: int | None = None,
    claude_settings_file: Path | str | None = None,
    claude_mcp_file: Path | str | None = None,
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
    provider = provider_command[0] if isinstance(provider_command, list) and provider_command else None
    if provider not in {"claude", "codex"}:
        raise NarrowError("Docker agent command supports only the native Claude and Codex CLIs.")
    if (
        not isinstance(credential_lease, CredentialLease)
        or credential_lease.provider != provider
        or credential_lease.task_id != task_id
        or credential_lease.execution_id != execution
    ):
        raise NarrowError("Docker agent command requires the exact selected-provider credential lease for this execution.")
    secret_path = validate_credential_lease(credential_lease)
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
    workspace = _validated_mount_source(workspace_path, directory=True)
    task_home = _validated_mount_source(task_home_path, directory=True)
    manifest = _validated_mount_source(policy_manifest_path, directory=False)
    read_policy_manifest(
        Path(manifest),
        expected_task_id=task_id,
        expected_execution_id=execution,
    )
    secret = _validated_mount_source(secret_path, directory=False)
    proxy_secret = _validated_mount_source(proxy_token_path, directory=False)
    expected_proxy_secret = (
        Path(credential_lease.state_dir)
        / "runtime" / "agent" / task_id / execution / "proxy.token"
    )
    if Path(proxy_secret) != expected_proxy_secret.resolve(strict=True):
        raise NarrowError("Docker agent proxy capability must be the exact task/execution-scoped token file.")
    read_token_file(Path(proxy_secret))
    if os.name != "nt":
        secret_metadata = Path(secret).stat()
        proxy_metadata = Path(proxy_secret).stat()
        home_metadata = Path(task_home).stat()
        if stat.S_IMODE(secret_metadata.st_mode) != 0o400:
            raise NarrowError("Docker provider credential mount must have owner-read-only mode 0400.")
        if stat.S_IMODE(proxy_metadata.st_mode) != 0o400:
            raise NarrowError("Docker proxy capability mount must have owner-read-only mode 0400.")
        if secret_metadata.st_uid != uid or proxy_metadata.st_uid != uid or home_metadata.st_uid != uid:
            raise NarrowError("Docker agent UID must own the private credential, proxy capability, and task-home mounts.")

    normalized_provider = normalize_provider_argv(provider, provider_command)
    claude_settings = None
    claude_mcp = None
    if provider == "claude":
        if claude_settings_file is None or claude_mcp_file is None:
            raise NarrowError("Claude container execution requires task-specific settings and an empty MCP profile.")
        claude_settings = _validated_mount_source(claude_settings_file, directory=False)
        claude_mcp = _validated_mount_source(claude_mcp_file, directory=False)
        expected_settings = Path(credential_lease.state_dir) / "providers" / task_id / "claude-settings.json"
        expected_mcp = Path(credential_lease.state_dir) / "providers" / task_id / "claude-mcp.json"
        if Path(claude_settings).resolve(strict=True) != expected_settings.resolve(strict=True) or Path(claude_mcp).resolve(strict=True) != expected_mcp.resolve(strict=True):
            raise NarrowError("Claude container settings must be the exact task-scoped NARROW-generated files.")
        validate_claude_container_settings(claude_settings, claude_mcp)
    elif claude_settings_file is not None or claude_mcp_file is not None:
        raise NarrowError("Codex container execution does not accept Claude settings mounts.")
    network = f"narrow-agent-{task_id[:32]}-{execution.replace('-', '')[:12]}"
    container_name = f"narrow-agent-{task_id[:24]}-{execution.replace('-', '')[:12]}"
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
        "--mount", f"type=bind,source={proxy_secret},target=/run/secrets/proxy-token,readonly",
    ]
    if provider == "claude":
        command.extend([
            "--mount", f"type=bind,source={claude_settings},target=/run/narrow/claude-settings.json,readonly",
            "--mount", f"type=bind,source={claude_mcp},target=/run/narrow/claude-mcp.json,readonly",
        ])
    command.extend(agent_container_label_args(task_id, execution))
    for key, value in (
        ("HOME", "/home/narrow"),
        ("LANG", "C.UTF-8"),
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
        "--provider", provider,
        "--credential-file", "/run/secrets/provider-api-key",
        "--proxy-token-file", "/run/secrets/proxy-token",
        "--",
        *normalized_provider,
    ])
    return command
