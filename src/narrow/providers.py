"""Provider-native CLI adapters for NARROW.

The adapters deliberately use each provider's supported CLI interface instead of
scraping private transcript files. Only low-risk telemetry is written to the
NARROW journal; prompts, tool arguments, and assistant message bodies are not.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .core import NarrowError, Store, load_config, redact, task_workspace, write_internal_file


@dataclass(frozen=True)
class ProviderEvent:
    """A sanitized, provider-neutral event suitable for the local journal."""

    event_type: str
    payload: dict[str, Any]
    session_id: str | None = None


class ProviderAdapter:
    name = "base"
    executable = ""

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def build_command(
        self,
        *,
        workspace: Path,
        prompt_file: Path,
        session_id: str | None = None,
        model: str | None = None,
        max_turns: int = 40,
        settings_file: Path | None = None,
        max_budget_usd: float | None = None,
        enable_subagents: bool = False,
        effort: str | None = None,
        ultracode: bool = False,
    ) -> list[str]:
        raise NotImplementedError

    def parse_event(self, event: dict[str, Any]) -> ProviderEvent | None:
        raise NotImplementedError


class ClaudeCodeAdapter(ProviderAdapter):
    """Claude Code print-mode adapter using documented JSONL and hook support."""

    name = "claude"
    executable = "claude"
    # NARROW registers task/subagent lifecycle hooks whose agent metadata is
    # available in recent Claude Code builds. Older releases may run the base
    # adapter but cannot provide the full continuity telemetry.
    telemetry_minimum_version = (2, 1, 290)

    def build_command(
        self,
        *,
        workspace: Path,
        prompt_file: Path,
        session_id: str | None = None,
        model: str | None = None,
        max_turns: int = 40,
        settings_file: Path | None = None,
        max_budget_usd: float | None = None,
        enable_subagents: bool = False,
        effort: str | None = None,
        ultracode: bool = False,
    ) -> list[str]:
        if not prompt_file.is_file() or prompt_file.is_symlink():
            raise NarrowError(f"Claude prompt file is missing or unsafe: {prompt_file}")
        if effort is not None and effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise NarrowError("Claude effort must be one of: low, medium, high, xhigh, max.")
        if ultracode and not enable_subagents:
            raise NarrowError("Claude ultracode requires explicit --allow-subagents opt-in.")
        if ultracode and effort is not None:
            raise NarrowError("Do not combine --ultracode with --effort; ultracode selects its own provider mode.")
        command = [
            self.executable,
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "default",
            # No interactive approval handler exists in print mode. Deny calls
            # requiring human permission rather than silently approving them.
            "--permission-prompts", "none",
            "--max-turns", str(max_turns),
            "--input-format", "text",
            # Keep the built-in surface narrow. MCP, WebSearch, and WebFetch
            # are intentionally excluded from the default automated profile.
            "--tools", (
                "Bash,Read,Edit,Write,Glob,Grep,NotebookEdit,Agent,TaskCreate,TaskUpdate,TaskList,TaskGet"
                if enable_subagents else "Bash,Read,Edit,Write,Glob,Grep,NotebookEdit"
            ),
            "--disallowedTools", "mcp__*",
        ]
        if max_budget_usd is not None:
            if not isinstance(max_budget_usd, (int, float)) or isinstance(max_budget_usd, bool) or not 0 < float(max_budget_usd) <= 1000:
                raise NarrowError("Claude max_budget_usd must be greater than 0 and no more than 1000.")
            command.extend(["--max-budget-usd", f"{float(max_budget_usd):.2f}"])
        if settings_file is not None:
            if not settings_file.is_file() or settings_file.is_symlink():
                raise NarrowError(f"Claude hook settings are missing or unsafe: {settings_file}")
            mcp_config = settings_file.with_name("claude-mcp.json")
            if not mcp_config.is_file() or mcp_config.is_symlink():
                raise NarrowError(f"Claude's NARROW-managed empty MCP config is missing or unsafe: {mcp_config}")
            command.extend(["--settings", str(settings_file), "--strict-mcp-config", "--mcp-config", str(mcp_config)])
        if model:
            command.extend(["--model", model])
        if ultracode:
            # Provider-managed dynamic workflows are a high-autonomy opt-in.
            command.extend(["--effort", "ultracode"])
        elif effort:
            # Effort availability is model/account dependent. The provider CLI
            # remains authoritative and will report unsupported combinations.
            command.extend(["--effort", effort])
        if session_id:
            command.extend(["--resume", session_id])
        # Pass the complete task prompt over stdin, not argv. This avoids OS
        # command-line length limits and keeps task text out of process listings.
        command.append("Follow the complete NARROW task instructions supplied on standard input.")
        return command

    def parse_event(self, event: dict[str, Any]) -> ProviderEvent | None:
        kind = str(event.get("type") or "unknown")[:80]
        subtype = str(event.get("subtype") or "")[:80]
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            session_id = None
        payload: dict[str, Any] = {"provider": self.name, "event": kind}
        if subtype:
            payload["subtype"] = subtype
        if kind == "system" and subtype == "init":
            payload["model"] = _bounded_text(event.get("model"), 100)
            payload["cwd"] = _bounded_text(event.get("cwd"), 300)
            payload["tools"] = _bounded_names(event.get("tools"), 40)
            return ProviderEvent("PROVIDER_SESSION_STARTED", payload, session_id)
        if kind == "assistant":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            names = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = _bounded_text(block.get("name"), 80)
                        if name:
                            names.append(name)
            if names:
                payload["tools"] = names[:20]
                return ProviderEvent("PROVIDER_TOOL_ACTIVITY", payload, session_id)
            return ProviderEvent("PROVIDER_ASSISTANT_ACTIVITY", payload, session_id)
        if kind == "user":
            # User events can contain tool results and secrets. Never store them.
            return ProviderEvent("PROVIDER_TOOL_RESULT", payload, session_id)
        if kind == "result":
            payload["is_error"] = bool(event.get("is_error", False))
            for source, target in (("num_turns", "turns"), ("duration_ms", "duration_ms"), ("total_cost_usd", "estimated_cost_usd")):
                value = event.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    payload[target] = value
            payload["result_subtype"] = subtype
            return ProviderEvent("PROVIDER_TURN_FINISHED", payload, session_id)
        if kind in {"hook_started", "hook_progress", "hook_response"}:
            payload["hook_event"] = _bounded_text(event.get("hook_event_name") or event.get("event"), 80)
            return ProviderEvent("PROVIDER_HOOK_ACTIVITY", payload, session_id)
        if kind in {"error", "system"}:
            return ProviderEvent("PROVIDER_NOTICE", payload, session_id)
        return None


class CodexCliAdapter(ProviderAdapter):
    """Codex CLI adapter using `codex exec --json` and resumable thread IDs."""

    name = "codex"
    executable = "codex"

    def build_command(
        self,
        *,
        workspace: Path,
        prompt_file: Path,
        session_id: str | None = None,
        model: str | None = None,
        max_turns: int = 40,
        settings_file: Path | None = None,
        max_budget_usd: float | None = None,
        enable_subagents: bool = False,
        effort: str | None = None,
        ultracode: bool = False,
    ) -> list[str]:
        if not prompt_file.is_file() or prompt_file.is_symlink():
            raise NarrowError(f"Codex prompt file is missing or unsafe: {prompt_file}")
        if max_budget_usd is not None:
            raise NarrowError("The Codex CLI adapter does not expose a NARROW-compatible per-run USD budget flag. Configure the provider's own usage limits instead.")
        if effort is not None and effort not in {"low", "medium", "high", "xhigh"}:
            raise NarrowError("Codex reasoning effort must be one of: low, medium, high, xhigh.")
        if ultracode:
            raise NarrowError("Claude ultracode is not a Codex CLI setting.")
        if session_id:
            command = [self.executable, "exec", "resume", session_id]
        else:
            command = [self.executable, "exec"]
        command.extend([
            "--json",
            "--cd", str(workspace),
            "--sandbox", "workspace-write",
            # In exec mode there is no interactive approval dialog. `never`
            # means the CLI will not pause for approval; actions outside its
            # sandbox remain denied rather than receiving an implicit grant.
            "--ask-for-approval", "never",
        ])
        if model:
            command.extend(["--model", model])
        if effort:
            # Codex exposes reasoning effort through its documented config
            # override mechanism. Enum validation above prevents TOML injection.
            command.extend(["--config", f'model_reasoning_effort="{effort}"'])
        # Codex exec documents '-' as a prompt-from-stdin sentinel.
        command.append("-")
        return command

    def parse_event(self, event: dict[str, Any]) -> ProviderEvent | None:
        kind = str(event.get("type") or "unknown")[:100]
        session_id = event.get("thread_id") or event.get("threadId") or event.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            session_id = None
        payload: dict[str, Any] = {"provider": self.name, "event": kind}
        item = event.get("item")
        if isinstance(item, dict):
            item_type = _bounded_text(item.get("type"), 80)
            item_name = _bounded_text(item.get("name"), 80)
            if item_type:
                payload["item_type"] = item_type
            if item_name:
                payload["item_name"] = item_name
        if kind == "thread.started":
            return ProviderEvent("PROVIDER_SESSION_STARTED", payload, session_id)
        if kind in {"turn.started", "turn.completed", "turn.failed", "turn.cancelled"}:
            turn = event.get("turn")
            if isinstance(turn, dict):
                status = _bounded_text(turn.get("status"), 40)
                if status:
                    payload["status"] = status
                error = turn.get("error")
                if isinstance(error, dict):
                    # Preserve only an error category/code, never the raw message.
                    code = _bounded_text(error.get("code") or error.get("type"), 80)
                    if code:
                        payload["error_code"] = code
            event_type = "PROVIDER_TURN_FINISHED" if kind in {"turn.completed", "turn.failed", "turn.cancelled"} else "PROVIDER_TURN_ACTIVITY"
            return ProviderEvent(event_type, payload, session_id)
        if kind in {"item.started", "item.completed", "item.failed"}:
            return ProviderEvent("PROVIDER_ITEM_ACTIVITY", payload, session_id)
        if kind == "error":
            error = event.get("error")
            if isinstance(error, dict):
                payload["error_code"] = _bounded_text(error.get("code") or error.get("type"), 80)
            return ProviderEvent("PROVIDER_NOTICE", payload, session_id)
        return None


_ADAPTERS: dict[str, ProviderAdapter] = {
    "claude": ClaudeCodeAdapter(),
    "codex": CodexCliAdapter(),
}


def _valid_session_id(value: Any) -> bool:
    # Provider output is untrusted. Keep IDs bounded and argument-safe before
    # ever placing them on a CLI command line.
    return isinstance(value, str) and len(value) <= 128 and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value))


def get_adapter(name: str) -> ProviderAdapter:
    adapter = _ADAPTERS.get(name.lower().strip())
    if adapter is None:
        raise NarrowError(f"Unsupported provider '{name}'. Choose one of: {', '.join(_ADAPTERS)}.")
    return adapter


def _parse_version(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    match = re.search(r"\b(?:v)?(\d+)\.(\d+)\.(\d+)\b", value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _version_label(value: tuple[int, int, int] | None) -> str | None:
    return ".".join(str(part) for part in value) if value else None


def provider_status(*, probe_versions: bool = False) -> list[dict[str, Any]]:
    """Report installed CLI executables without reading provider credentials.

    Version probing is opt-in because it executes the binary found on PATH.
    The probe only invokes the documented, non-mutating --version flag.
    """
    result = []
    for name, adapter in _ADAPTERS.items():
        path = shutil.which(adapter.executable)
        minimum = getattr(adapter, "telemetry_minimum_version", None)
        item: dict[str, Any] = {
            "name": name,
            "available": bool(path),
            "path": path,
            "version": None,
            "minimum_version": _version_label(minimum),
            "compatibility": "not_checked" if minimum else "not_version_gated",
        }
        if path and probe_versions:
            try:
                completed = subprocess.run(
                    [path, "--version"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=4,
                    check=False,
                    shell=False,
                )
                if completed.returncode == 0:
                    item["version"] = _bounded_text(completed.stdout, 160)
                    parsed = _parse_version(item["version"])
                    if minimum:
                        if parsed is None:
                            item["compatibility"] = "unknown"
                            item["compatibility_note"] = "Version string could not be parsed; update or verify manually."
                        elif parsed < minimum:
                            item["compatibility"] = "upgrade_recommended"
                            item["compatibility_note"] = f"Full Claude task/subagent telemetry requires { _version_label(minimum) } or newer."
                        else:
                            item["compatibility"] = "compatible"
                else:
                    item["version_error"] = f"version probe exited {completed.returncode}"
                    item["compatibility"] = "unknown"
            except subprocess.TimeoutExpired:
                item["version_error"] = "version probe timed out"
                item["compatibility"] = "unknown"
            except OSError as exc:
                item["version_error"] = f"version probe failed: {type(exc).__name__}"
                item["compatibility"] = "unknown"
        result.append(item)
    return result


def latest_provider_session_id(store: Store, task_id: str, provider: str) -> str | None:
    events = store.events(task_id, limit=1000)
    for event in reversed(events):
        if event["type"] == "PROVIDER_SESSION_STARTED" and event["payload"].get("provider") == provider:
            value = event["payload"].get("session_id")
            if _valid_session_id(value):
                return value
    return None


def _visible_assistant_text(raw_event: dict[str, Any], adapter: ProviderAdapter) -> str | None:
    """Return user-facing assistant text for the terminal, never for the journal."""
    blocks: list[str] = []
    if adapter.name == "claude" and raw_event.get("type") == "assistant":
        message = raw_event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    blocks.append(block["text"])
    elif adapter.name == "codex" and raw_event.get("type") in {"item.completed", "item.started"}:
        item = raw_event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            blocks.append(item["text"])
    text = "\n".join(part.strip() for part in blocks if part.strip())
    return redact(text[:60000]) if text else None


def make_event_callback(store: Store, task_id: str, adapter: ProviderAdapter) -> Callable[[dict[str, Any]], None]:
    def record(raw_event: dict[str, Any]) -> None:
        parsed = adapter.parse_event(raw_event)
        if parsed is None:
            return
        # Only sanitized metadata is persisted. The response body is displayed
        # for the user but never copied into NARROW's local event journal.
        payload = dict(parsed.payload)
        if parsed.session_id:
            payload["session_id"] = parsed.session_id
        store.add_event(task_id, parsed.event_type, payload)
        visible_text = _visible_assistant_text(raw_event, adapter)
        if visible_text:
            print("\n" + visible_text + "\n", flush=True)
        if parsed.event_type == "PROVIDER_SESSION_STARTED" and parsed.session_id:
            print(f"\nNARROW attached {adapter.name} session: {parsed.session_id}")
        elif parsed.event_type == "PROVIDER_TOOL_ACTIVITY":
            tools = payload.get("tools") or []
            if tools:
                print(f"\n[{adapter.name}] using: {', '.join(tools)}")
        elif parsed.event_type == "PROVIDER_ITEM_ACTIVITY":
            label = payload.get("item_name") or payload.get("item_type")
            if label:
                print(f"\n[{adapter.name}] {payload['event']}: {label}")
        elif parsed.event_type == "PROVIDER_TURN_FINISHED":
            details = []
            if payload.get("status"):
                details.append(str(payload["status"]))
            if "turns" in payload:
                details.append(f"{payload['turns']} turns")
            if "estimated_cost_usd" in payload:
                details.append(f"estimated ${payload['estimated_cost_usd']:.4f}")
            print(f"\n[{adapter.name}] provider turn finished" + (f" ({', '.join(details)})" if details else ""))
    return record


def _bounded_text(value: Any, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:max_length] if cleaned else None


def _bounded_names(value: Any, maximum: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:maximum]:
        text = _bounded_text(item, 80)
        if text:
            result.append(text)
    return result


def _hook_command(event: str) -> str:
    argv = [sys.executable, "-m", "narrow", "hook", "claude", event]
    # Claude hooks accept a command string rather than a separate argv array.
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _claude_allowed_domains(store: Store) -> list[str]:
    """Load an explicit, narrow outbound-host allowlist from local config."""
    config = load_config(store.root)
    domains = config.get("claude_network_allowed_domains", [])
    if not isinstance(domains, list) or len(domains) > 100:
        raise NarrowError("claude_network_allowed_domains must be an array of at most 100 entries.")
    validated: list[str] = []
    pattern = re.compile(r"^(?:\*\.)?[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?(?::[0-9]{1,5})?$")
    for raw in domains:
        if not isinstance(raw, str) or len(raw) > 253 or raw != raw.strip() or not pattern.fullmatch(raw):
            raise NarrowError(f"Invalid Claude sandbox network domain '{raw}'. Use a hostname or *.hostname with an optional port; do not include a URL or path.")
        host = raw.rsplit(":", 1)[0] if ":" in raw else raw
        if host in {"*", "*.com", "*.net", "*.org", "*.io", "*.dev"} or ".." in host:
            raise NarrowError(f"Claude sandbox network domain '{raw}' is too broad or malformed.")
        port = raw.rsplit(":", 1)[1] if ":" in raw else None
        if port is not None and not 1 <= int(port) <= 65535:
            raise NarrowError(f"Claude sandbox network domain '{raw}' has an invalid port.")
        normalized = raw.lower()
        if normalized not in validated:
            validated.append(normalized)
    return validated


def _credential_env_names() -> list[dict[str, str]]:
    """Return secret-like environment variable names, never their values."""
    known = {
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "GITHUB_TOKEN", "GH_TOKEN",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "NPM_TOKEN",
        "PYPI_TOKEN", "HUGGINGFACE_TOKEN", "HF_TOKEN", "AZURE_CLIENT_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS", "DATABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
    }
    discovered = {
        name for name in os.environ
        if re.search(r"(?:TOKEN|API_KEY|SECRET|PASSWORD|CREDENTIAL|AUTH)$", name, re.IGNORECASE)
    }
    return [{"name": name, "mode": "deny"} for name in sorted(known | discovered)[:200]]


def claude_settings_path(store: Store, task_id: str, *, enable_subagents: bool = False) -> Path:
    """Write task-local hooks and a strict Bash sandbox policy for Claude Code.

    Claude's OS sandbox applies to shell commands and their subprocesses, not
    its in-process file tools or external MCP processes. NARROW therefore also
    gates direct Read/Glob/Grep/Write/Edit tools and disables MCP for this
    automated profile. The Codex CLI has its own workspace-write sandbox.
    """
    task = store.get_task(task_id)
    workspace = task_workspace(task)
    providers_root = store.state_dir / "providers"
    if providers_root.is_symlink() or (providers_root.exists() and not providers_root.is_dir()):
        raise NarrowError("NARROW provider state path is linked or not a directory.")
    providers_root.mkdir(exist_ok=True)
    providers_dir = providers_root / task_id
    if providers_dir.is_symlink() or (providers_dir.exists() and not providers_dir.is_dir()):
        raise NarrowError("NARROW task provider state path is linked or not a directory.")
    providers_dir.mkdir(exist_ok=True)
    hooks = {
        "SessionStart": [{"hooks": [{
            "type": "command",
            "command": _hook_command("session-start"),
            "timeout": 5,
        }]}],
        "PreToolUse": [{
            "matcher": "Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit",
            "hooks": [{
                "type": "command",
                "command": _hook_command("pre-tool-use"),
                "timeout": 5,
                "onFailure": "block",
            }],
        }],
        "PostToolUse": [{
            "matcher": "Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit",
            "hooks": [{
                "type": "command",
                "command": _hook_command("post-tool-use"),
                "timeout": 5,
            }],
        }],
        "PostToolUseFailure": [{
            "matcher": "Bash|Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit|Agent",
            "hooks": [{
                "type": "command",
                "command": _hook_command("post-tool-use-failure"),
                "timeout": 5,
                "onFailure": "continue",
            }],
        }],
        "SubagentStart": [{"hooks": [{
            "type": "command", "command": _hook_command("subagent-start"),
            "timeout": 5, "onFailure": "continue",
        }]}],
        "SubagentStop": [{"hooks": [{
            "type": "command", "command": _hook_command("subagent-stop"),
            "timeout": 5, "onFailure": "continue",
        }]}],
        "TaskCreated": [{"hooks": [{
            "type": "command", "command": _hook_command("task-created"),
            "timeout": 5, "onFailure": "continue",
        }]}],
        "TaskCompleted": [{"hooks": [{
            "type": "command", "command": _hook_command("task-completed"),
            "timeout": 5, "onFailure": "continue",
        }]}],
        "PreCompact": [{"hooks": [{
            "type": "command",
            "command": _hook_command("pre-compact"),
            "timeout": 10,
        }]}],
        "Stop": [{"hooks": [{
            "type": "command",
            "command": _hook_command("stop"),
            "timeout": 5,
        }]}],
        "SessionEnd": [{"hooks": [{
            "type": "command",
            "command": _hook_command("session-end"),
            "timeout": 5,
        }]}],
    }
    settings = {
        "hooks": hooks,
        "permissions": {
            "defaultMode": "default",
            # The OS sandbox applies this read boundary to shell commands.
            # Direct file-tool reads are separately gated by the NARROW hook.
            "blockReadsOutsideWorkingDirectories": True,
        },
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {
                "denyRead": [
                    "~/.ssh", "~/.aws", "~/.azure", "~/.kube", "~/.docker/config.json",
                    "~/.netrc", "~/.npmrc", "~/.pypirc", "~/.git-credentials",
                    "~/.config/gh/hosts.yml", "~/.config/gcloud",
                ],
            },
            "credentials": {
                "envVars": _credential_env_names(),
                "files": [
                    {"path": "~/.ssh", "mode": "deny"},
                    {"path": "~/.aws", "mode": "deny"},
                    {"path": "~/.azure", "mode": "deny"},
                    {"path": "~/.kube", "mode": "deny"},
                    {"path": "~/.docker/config.json", "mode": "deny"},
                    {"path": "~/.netrc", "mode": "deny"},
                    {"path": "~/.npmrc", "mode": "deny"},
                    {"path": "~/.pypirc", "mode": "deny"},
                    {"path": "~/.git-credentials", "mode": "deny"},
                    {"path": "~/.config/gh/hosts.yml", "mode": "deny"},
                    {"path": "~/.config/gcloud", "mode": "deny"},
                ],
            },
            "network": {
                "strictAllowlist": True,
                "allowedDomains": _claude_allowed_domains(store),
            },
        },
    }
    path = providers_dir / "claude-settings.json"
    write_internal_file(providers_dir, path.name, json.dumps(settings, indent=2) + "\n")
    mcp_path = providers_dir / "claude-mcp.json"
    write_internal_file(providers_dir, mcp_path.name, json.dumps({"mcpServers": {}}, indent=2) + "\n")
    # Ensure generated settings can't be swapped for links or hard links.
    for generated in (path, mcp_path):
        if not generated.is_file() or generated.is_symlink() or generated.stat().st_nlink != 1:
            raise NarrowError(f"Generated Claude task policy did not pass NARROW's file integrity checks: {generated.name}")
    store.add_event(task_id, "PROVIDER_HOOKS_INSTALLED", {
        "provider": "claude",
        "workspace": str(workspace),
        "settings_path": str(path),
        "mcp_config_path": str(mcp_path),
        "protected_tool_families": ["Read", "Glob", "Grep", "Write", "Edit", "MultiEdit", "NotebookEdit"],
        "subagents_enabled": bool(enable_subagents),
        "shell_sandbox": "required_fail_closed",
        "shell_network_allowlist_count": len(settings["sandbox"]["network"]["allowedDomains"]),
        "mcp_enabled": False,
    })
    return path


def build_provider_command(
    store: Store,
    task_id: str,
    provider: str,
    prompt_file: Path,
    *,
    resume: bool = False,
    model: str | None = None,
    max_turns: int = 40,
    max_budget_usd: float | None = None,
    enable_subagents: bool = False,
    effort: str | None = None,
    ultracode: bool = False,
) -> tuple[ProviderAdapter, list[str]]:
    adapter = get_adapter(provider)
    if not adapter.available():
        raise NarrowError(
            f"{adapter.executable} was not found on PATH. Install and authenticate {adapter.name} separately; NARROW never asks for provider credentials."
        )
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
        raise NarrowError("Task ID is invalid for provider adapter state.")
    task = store.get_task(task_id)
    workspace = task_workspace(task)
    session_id = latest_provider_session_id(store, task_id, adapter.name) if resume else None
    settings_file = claude_settings_path(store, task_id, enable_subagents=enable_subagents) if adapter.name == "claude" else None
    command = adapter.build_command(
        workspace=workspace,
        prompt_file=prompt_file,
        session_id=session_id,
        model=model,
        max_turns=max_turns,
        settings_file=settings_file,
        max_budget_usd=max_budget_usd,
        enable_subagents=enable_subagents,
        effort=effort,
        ultracode=ultracode,
    )
    if resume and not session_id:
        store.add_event(task_id, "PROVIDER_RESUME_WITH_HANDOFF", {
            "provider": adapter.name,
            "reason": "No provider session identifier was emitted; continuing in a new session from the saved NARROW handoff.",
        })
    return adapter, command
