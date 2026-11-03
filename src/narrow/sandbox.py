"""Optional hardened Docker/Podman execution for verification commands."""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .core import NarrowError

@dataclass(frozen=True)
class SandboxPlan:
    command: list[str]
    engine: str
    container_name: str

def _validated_options(config: dict[str, Any]) -> tuple[str, str, str, str, int, bool]:
    mode = config.get("mode", "off")
    if mode not in {"off", "auto", "docker", "podman"}:
        raise NarrowError("verification_sandbox.mode must be off, auto, docker, or podman.")
    if mode == "off":
        return mode, "", "", "2", 256, False
    image = config.get("image")
    if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,250}", image):
        raise NarrowError("verification_sandbox.image must name a pre-built local image.")
    memory = config.get("memory", "2g")
    if not isinstance(memory, str) or not re.fullmatch(r"[1-9][0-9]*(?:m|g)", memory.lower()):
        raise NarrowError("verification_sandbox.memory must look like 512m or 2g.")
    cpus = config.get("cpus", "2")
    if isinstance(cpus, (int, float)) and not isinstance(cpus, bool):
        cpus = str(cpus)
    if not isinstance(cpus, str) or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", cpus):
        raise NarrowError("verification_sandbox.cpus must be a positive number.")
    if not 0.1 <= float(cpus) <= 32:
        raise NarrowError("verification_sandbox.cpus must be between 0.1 and 32.")
    pids = config.get("pids_limit", 256)
    if not isinstance(pids, int) or isinstance(pids, bool) or not 32 <= pids <= 8192:
        raise NarrowError("verification_sandbox.pids_limit must be between 32 and 8192.")
    read_only = config.get("workspace_read_only", False)
    if not isinstance(read_only, bool):
        raise NarrowError("verification_sandbox.workspace_read_only must be true or false.")
    return mode, image, memory, cpus, pids, read_only

def prepare_sandbox_command(command: list[str], cwd: Path, config: dict[str, Any]) -> SandboxPlan | None:
    """Build a fail-closed local container command; never pulls an image."""
    mode, image, memory, cpus, pids, read_only = _validated_options(config)
    if mode == "off":
        return None
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item and "\x00" not in item for item in command):
        raise NarrowError("Containerized verification requires a non-empty argv command.")
    engine_path = None
    for candidate in (("docker", "podman") if mode == "auto" else (mode,)):
        engine_path = shutil.which(candidate)
        if engine_path:
            break
    if not engine_path:
        raise NarrowError(f"No {mode} container engine was found on PATH. Verification was not run.")
    try:
        workspace = Path(cwd).resolve(strict=True)
    except OSError as exc:
        raise NarrowError("Task workspace cannot be resolved for container execution.") from exc
    if not workspace.is_dir() or Path(cwd).is_symlink():
        raise NarrowError("Task workspace is missing or redirected.")
    if "," in str(workspace):
        raise NarrowError("Container verification does not support workspace paths containing commas.")
    try:
        inspected = subprocess.run([engine_path, "image", "inspect", image], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NarrowError(f"Could not inspect the local container image: {type(exc).__name__}.") from exc
    if inspected.returncode != 0:
        raise NarrowError(f"Container image {image!r} is not present locally. Build or pull it yourself first; NARROW never downloads images.")
    container_name = f"narrow-check-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    workspace_text = str(workspace).replace("\\", "/") if os.name == "nt" else str(workspace)
    mount_mode = "ro" if read_only else "rw"
    argv = [
        engine_path, "run", "--rm", "--name", container_name,
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit", str(pids), "--memory", memory, "--cpus", cpus,
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--mount", f"type=bind,src={workspace_text},dst=/workspace,{mount_mode}",
        "--workdir", "/workspace", "--env", "PYTHONDONTWRITEBYTECODE=1",
    ]
    if os.name != "nt" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        argv.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    argv.extend(["--entrypoint", command[0], image, *command[1:]])
    return SandboxPlan(argv, engine_path, container_name)

def cleanup_container(engine: str, container_name: str) -> None:
    """Best-effort cleanup after timeout or interruption."""
    try:
        subprocess.run([engine, "rm", "--force", container_name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
