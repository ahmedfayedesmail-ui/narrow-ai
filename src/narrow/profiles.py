"""Safe, review-first project profiles and an explicit security posture report.

Profiles are onboarding suggestions. They are not OS security boundaries and
never enable agent/container isolation by themselves.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_PROFILE_DIRS = {
    "web": ("src", "app", "pages", "components", "lib", "tests", "test"),
    "python": ("src", "tests", "test", "scripts"),
    "data": ("src", "tests", "test", "scripts", "notebooks", "pipelines"),
    "general": ("src", "app", "lib", "tests", "test"),
}
_PROFILE_DESCRIPTIONS = {
    "web": "Web application: source/UI folders and test folders only.",
    "python": "Python project: source, scripts, and test folders only.",
    "data": "Data/ML project: source, notebooks, pipelines, and test folders; raw data folders are excluded.",
    "general": "General repository: common source and test folders only.",
}
_RESERVED_DIRS = {".", "..", ".git", ".narrow", ".ssh", ".aws", ".config"}


def _detect_profile(root: Path, discovery: dict[str, Any]) -> str:
    top_dirs = set(discovery.get("top_level_directories", []))
    languages = set(discovery.get("languages", []))
    if "Python" in languages and "notebooks" in top_dirs:
        return "data"
    if "JavaScript/TypeScript" in languages:
        return "web"
    if "Python" in languages:
        return "python"
    return "general"


def build_setup_plan(
    root: Path,
    discovery: dict[str, Any],
    requested_profile: str = "auto",
) -> dict[str, Any]:
    """Build a bounded read-only onboarding plan from project discovery."""
    root = Path(root).resolve(strict=True)
    selected = _detect_profile(root, discovery) if requested_profile == "auto" else requested_profile
    if selected not in _PROFILE_DIRS:
        raise ValueError("Unsupported project profile.")

    top_dirs = set(discovery.get("top_level_directories", []))
    allowed: list[str] = []
    for name in _PROFILE_DIRS[selected]:
        candidate = root / name
        if name in top_dirs and name not in _RESERVED_DIRS and not candidate.is_symlink() and candidate.is_dir():
            resolved = candidate.resolve(strict=True)
            if resolved.is_relative_to(root):
                allowed.append(f"{name}/**")
    if not allowed:
        allowed = [
            item for item in discovery.get("suggested_allow", [])
            if isinstance(item, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}/\*\*", item)
            and (root / item[:-3]).is_dir()
            and not (root / item[:-3]).is_symlink()
        ]

    denied = [
        item for item in discovery.get("suggested_deny", [])
        if isinstance(item, str) and len(item) <= 240 and item
    ]
    checks: list[dict[str, Any]] = []
    for item in discovery.get("checks", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        command = item.get("command")
        timeout = item.get("timeout_seconds")
        if (
            isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name)
            and isinstance(command, list) and 1 <= len(command) <= 32
            and all(isinstance(part, str) and part and "\x00" not in part for part in command)
            and isinstance(timeout, int) and not isinstance(timeout, bool) and 1 <= timeout <= 3600
        ):
            checks.append({
                "name": name,
                "command": list(command),
                "timeout_seconds": timeout,
                "reason": str(item.get("reason", "Detected from project manifests."))[:300],
            })

    return {
        "schema": "narrow.setup-plan.v1",
        "profile": selected,
        "profile_description": _PROFILE_DESCRIPTIONS[selected],
        "root": str(root),
        "languages": sorted(x for x in discovery.get("languages", []) if isinstance(x, str)),
        "frameworks": sorted(x for x in discovery.get("frameworks", []) if isinstance(x, str)),
        "allowed_paths": allowed,
        "denied_paths": denied,
        "checks": checks,
        "safety": {
            "git_workspace_is_os_sandbox": False,
            "agent_isolation": "Not enabled by setup. Native provider controls are provider-specific; generic agents inherit the invoking user's OS permissions unless separately isolated.",
            "verification_sandbox_default": "off",
            "human_review_required": True,
        },
        "requires_human_review": True,
        "writes_files": False,
        "executes_commands": False,
    }


def profile_scope_defaults(root: Path, profile: Any) -> tuple[list[str], list[str]]:
    """Return only safe, existing top-level directories from saved profile metadata."""
    root = Path(root).resolve(strict=True)
    if not isinstance(profile, dict) or profile.get("name") not in _PROFILE_DIRS:
        return [], []
    allowed: list[str] = []
    raw_allowed = profile.get("allowed_paths", [])
    if isinstance(raw_allowed, list):
        for item in raw_allowed[:20]:
            match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]{0,63})/\*\*", item) if isinstance(item, str) else None
            if not match:
                continue
            name = match.group(1)
            candidate = root / name
            if name in _RESERVED_DIRS or candidate.is_symlink() or not candidate.is_dir():
                continue
            try:
                if candidate.resolve(strict=True).is_relative_to(root) and item not in allowed:
                    allowed.append(item)
            except OSError:
                continue

    denied: list[str] = []
    raw_denied = profile.get("denied_paths", [])
    if isinstance(raw_denied, list):
        for item in raw_denied[:40]:
            if (
                isinstance(item, str) and item and len(item) <= 240 and "\x00" not in item
                and not item.startswith("/") and ":" not in item
                and ".." not in item.replace("\\", "/").split("/")
                and item not in denied
            ):
                denied.append(item)
    return allowed, denied


def build_security_posture(config: dict[str, Any], *, initialized: bool) -> dict[str, Any]:
    """Describe orchestration and OS-enforcement as separate, non-interchangeable layers."""
    sandbox = config.get("verification_sandbox", {})
    if not isinstance(sandbox, dict):
        sandbox = {}
    mode = sandbox.get("mode", "off")
    if mode not in {"off", "docker", "auto"}:
        mode = "invalid"
    image_configured = isinstance(sandbox.get("image"), str) and bool(sandbox.get("image", "").strip())
    if mode == "off":
        verification = "OFF: verification commands run with the invoking user's OS permissions."
    elif mode == "invalid":
        verification = "INVALID: the verification sandbox mode is not recognized; do not run checks until configuration is fixed."
    elif not image_configured:
        verification = f"{mode.upper()} selected but no image is configured; verification isolation is not ready."
    else:
        verification = f"{mode.upper()} configured for verification commands only; it does not automatically isolate the AI agent."

    project_profile = config.get("project_profile")
    profile_name = project_profile.get("name") if isinstance(project_profile, dict) else None
    if profile_name not in _PROFILE_DIRS:
        profile_name = None

    return {
        "schema": "narrow.security-posture.v1",
        "initialized": bool(initialized),
        "project_profile": profile_name,
        "orchestration": {
            "workspace": "Task-scoped Git copy without configured remotes.",
            "scope_monitor": "Allowed/denied paths are monitored before, during, and after supervised work.",
            "execution_budget": "Agent and repair loops are bounded by configured limits.",
            "completion_gate": "Passing checks move work to review; human acceptance remains required.",
        },
        "security_enforcement": {
            "git_workspace_is_os_sandbox": False,
            "agent_runtime": "A Git copy is not OS isolation. Native provider enforcement is provider-specific. Generic --agent and direct run/resume commands retain invoking-user permissions unless separately sandboxed.",
            "codex_container_runtime": "Experimental opt-in POSIX/local-Docker profile only; not a microVM or universal sandbox.",
            "verification_runtime": verification,
            "network": "No universal agent-network boundary is implied. Network controls depend on the selected provider or explicit container profile.",
        },
        "defaults": {
            "verification_sandbox_mode": mode,
            "human_acceptance_required": True,
            "setup_runs_commands": False,
        },
        "limitations": [
            "A Git worktree isolates repository edits, not filesystem, process, or network permissions.",
            "Docker daemon and host kernel remain trusted when container profiles are used.",
            "A passing test suite is evidence about configured checks, not proof of semantic correctness or sandbox security.",
        ],
    }
