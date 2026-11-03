from __future__ import annotations

import re

from .core import NarrowError

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
