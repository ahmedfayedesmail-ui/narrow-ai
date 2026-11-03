from __future__ import annotations

import re
from narrow.core import NarrowError

_IMAGE_DIGEST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}@sha256:[0-9a-f]{64}$")


def validate_agent_image(image: str) -> str:
    """Require an immutable OCI digest reference; mutable tags are refused."""
    if not isinstance(image, str) or not _IMAGE_DIGEST.fullmatch(image):
        raise NarrowError("Agent image must be pinned by an immutable @sha256:<64 hex> digest; mutable tags are refused.")
    return image


def normalize_provider_argv(provider: str, command: list[str]) -> list[str]:
    """Rewrite Codex's host workspace path to /workspace and preserve guardrails.

    Claude is refused until host-generated settings and hooks are replaced by
    a container-specific profile with container-local paths.
    """
    if provider != "codex":
        raise NarrowError("The first container launch profile supports Codex only; Claude needs a container-specific settings and hook profile.")
    if (
        not isinstance(command, list)
        or len(command) < 7
        or command[0] != "codex"
        or command[1] != "exec"
        or any(not isinstance(item, str) or not item or "\x00" in item for item in command)
    ):
        raise NarrowError("Container provider command must be a validated Codex exec argv array.")
    result = list(command)
    cd_positions = [index for index, value in enumerate(result[:-1]) if value == "--cd"]
    if len(cd_positions) != 1:
        raise NarrowError("Codex container command must contain exactly one --cd workspace argument.")
    cd_index = cd_positions[0]
    result[cd_index + 1] = "/workspace"
    if "--sandbox" not in result or result[result.index("--sandbox") + 1:result.index("--sandbox") + 2] != ["workspace-write"]:
        raise NarrowError("Codex container command must retain --sandbox workspace-write.")
    if "--ask-for-approval" not in result or result[result.index("--ask-for-approval") + 1:result.index("--ask-for-approval") + 2] != ["never"]:
        raise NarrowError("Codex container command must retain --ask-for-approval never.")
    for index, value in enumerate(result):
        if index == cd_index + 1:
            continue
        if value.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            raise NarrowError("Host filesystem paths are forbidden in container provider arguments.")
    return result
