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


def _normalize_codex_argv(command: list[str]) -> list[str]:
    if (
        not isinstance(command, list)
        or any(not isinstance(item, str) or not item or "\\x00" in item for item in command)
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
        if len(model) > 120 or model.startswith("-") or "/" in model or "\\\\" in model:
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


def _normalize_claude_argv(command: list[str]) -> list[str]:
    """Rebuild the bounded Claude print-mode command and relocate policy files."""
    sentinel = "Follow the complete NARROW task instructions supplied on standard input."
    if (
        not isinstance(command, list)
        or any(not isinstance(item, str) or not item or "\\x00" in item for item in command)
        or len(command) < 22
        or command[0] != "claude"
        or command[-1] != sentinel
    ):
        raise NarrowError("Container provider command must match the NARROW Claude print-mode adapter contract.")

    tokens = command[1:-1]
    prefix = [
        "--print", "--output-format", "stream-json", "--verbose",
        "--permission-mode", "default", "--permission-prompts", "none",
        "--max-turns",
    ]
    if tokens[:len(prefix)] != prefix or len(tokens) <= len(prefix):
        raise NarrowError("Claude container command must use structured print mode and deny approval prompts.")
    cursor = len(prefix)
    try:
        turns = int(tokens[cursor])
    except (TypeError, ValueError) as exc:
        raise NarrowError("Claude container max-turn budget is malformed.") from exc
    if not 1 <= turns <= 200:
        raise NarrowError("Claude container max-turn budget must be between 1 and 200.")
    cursor += 1
    if tokens[cursor:cursor + 2] != ["--input-format", "text"]:
        raise NarrowError("Claude container command must read its full task prompt from standard input.")
    cursor += 2
    allowed_tools = "Bash,Read,Edit,Write,Glob,Grep,NotebookEdit"
    if tokens[cursor:cursor + 2] != ["--tools", allowed_tools]:
        raise NarrowError("Claude container profile refuses unreviewed tools and subagent expansion.")
    cursor += 2
    if tokens[cursor:cursor + 2] != ["--disallowedTools", "mcp__*"]:
        raise NarrowError("Claude container profile must disable MCP tools.")
    cursor += 2

    result = [
        "claude", "--print", "--output-format", "stream-json", "--verbose",
        "--permission-mode", "default", "--permission-prompts", "none",
        "--max-turns", str(turns), "--input-format", "text",
        "--tools", allowed_tools, "--disallowedTools", "mcp__*",
    ]
    settings_seen = False
    mcp_seen = False
    strict_mcp_seen = False
    seen: set[str] = set()
    while cursor < len(tokens):
        flag = tokens[cursor]
        if flag == "--strict-mcp-config":
            if strict_mcp_seen:
                raise NarrowError("Claude container command repeats --strict-mcp-config.")
            strict_mcp_seen = True
            result.append(flag)
            cursor += 1
            continue
        if flag not in {"--settings", "--mcp-config", "--model", "--effort", "--max-budget-usd", "--resume"}:
            raise NarrowError(f"Claude container command contains an unsupported option: {flag}.")
        if flag in seen or cursor + 1 >= len(tokens):
            raise NarrowError(f"Claude container command repeats {flag} or omits its value.")
        seen.add(flag)
        value = tokens[cursor + 1]
        if not value or value.startswith("--"):
            raise NarrowError(f"Claude container option {flag} has an invalid value.")
        if flag == "--settings":
            settings_seen = True
            result.extend([flag, "/run/narrow/claude-settings.json"])
        elif flag == "--mcp-config":
            mcp_seen = True
            result.extend([flag, "/run/narrow/claude-mcp.json"])
        elif flag == "--model":
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", value):
                raise NarrowError("Claude model value is invalid for container execution.")
            result.extend([flag, value])
        elif flag == "--effort":
            if value not in {"low", "medium", "high", "xhigh", "max"}:
                raise NarrowError("Claude container effort must be one of low, medium, high, xhigh, max.")
            result.extend([flag, value])
        elif flag == "--max-budget-usd":
            try:
                budget = float(value)
            except ValueError as exc:
                raise NarrowError("Claude container budget is malformed.") from exc
            if not 0 < budget <= 1000:
                raise NarrowError("Claude container budget must be greater than 0 and no more than 1000 USD.")
            result.extend([flag, f"{budget:.2f}"])
        elif flag == "--resume":
            if not _SESSION_ID.fullmatch(value):
                raise NarrowError("Claude resume session ID is invalid for container execution.")
            result.extend([flag, value])
        cursor += 2

    if not settings_seen or not mcp_seen or not strict_mcp_seen:
        raise NarrowError("Claude container command must use its task-specific settings and strict empty MCP profile.")
    result.append(sentinel)
    return result


def normalize_provider_argv(provider: str, command: list[str]) -> list[str]:
    """Rebuild a reviewed native provider argv and translate host-only paths."""
    if provider == "codex":
        return _normalize_codex_argv(command)
    if provider == "claude":
        return _normalize_claude_argv(command)
    raise NarrowError("The container agent runtime supports only the native Claude and Codex providers.")


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
    expected_top_level = {"permissions", "hooks"}
    if set(settings_data) != expected_top_level:
        raise NarrowError("Claude container settings contain unsupported or missing top-level fields.")
    permissions = settings_data.get("permissions")
    if permissions != {
        "defaultMode": "default",
        "blockReadsOutsideWorkingDirectories": True,
    }:
        raise NarrowError("Claude container settings must preserve default permissions and working-directory read restriction.")
    hooks = settings_data.get("hooks")
    expected_events = {
        "SessionStart": "session-start",
        "PreToolUse": "pre-tool-use",
        "PostToolUse": "post-tool-use",
        "PostToolUseFailure": "post-tool-use-failure",
        "SubagentStart": "subagent-start",
        "SubagentStop": "subagent-stop",
        "TaskCreated": "task-created",
        "TaskCompleted": "task-completed",
        "PreCompact": "pre-compact",
        "Stop": "stop",
        "SessionEnd": "session-end",
    }
    if not isinstance(hooks, dict) or set(hooks) != set(expected_events):
        raise NarrowError("Claude container hook profile is missing required NARROW lifecycle and file-policy hooks.")
    for event_name, event in expected_events.items():
        groups = hooks.get(event_name)
        if not isinstance(groups, list) or len(groups) != 1:
            raise NarrowError(f"Claude container {event_name} hook configuration is malformed.")
        group = groups[0]
        if not isinstance(group, dict):
            raise NarrowError(f"Claude container {event_name} hook group is malformed.")
        hook_list = group.get("hooks")
        if not isinstance(hook_list, list) or len(hook_list) != 1:
            raise NarrowError(f"Claude container {event_name} hook command is missing or ambiguous.")
        hook = hook_list[0]
        if not isinstance(hook, dict) or hook.get("type") != "command":
            raise NarrowError(f"Claude container {event_name} must use one in-image NARROW command hook.")
        if hook.get("command") != f"python -m narrow hook claude {event}":
            raise NarrowError(f"Claude container {event_name} hook does not invoke the exact NARROW in-image command.")
        if event_name == "PreToolUse":
            if group.get("matcher") != "Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit" or hook.get("onFailure") != "block":
                raise NarrowError("Claude container direct file tools must be gated by a blocking NARROW PreToolUse hook.")
            if hook.get("timeout") != 5:
                raise NarrowError("Claude container direct file gate timeout is invalid.")
        elif event_name == "PostToolUse":
            if group.get("matcher") != "Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit":
                raise NarrowError("Claude container PostToolUse hook matcher is invalid.")
        elif event_name == "PostToolUseFailure":
            if group.get("matcher") != "Bash|Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit|Agent":
                raise NarrowError("Claude container PostToolUseFailure matcher is invalid.")
        elif "matcher" in group:
            raise NarrowError(f"Claude container {event_name} hook must not broaden its matcher.")
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
