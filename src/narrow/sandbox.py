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

def _validated_options(config: dict[str, Any]) -> tuple[str, str, str, str, int, bool, list[str]]:
    mode = config.get("mode", "off")
    if not isinstance(mode, str) or mode not in {"off", "auto", "docker", "podman"}:
        raise NarrowError("verification_sandbox.mode must be off, auto, docker, or podman.")
    if mode == "off":
        return mode, "", "", "2", 256, False, []
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
    read_only = config.get("workspace_read_only", True)
    if not isinstance(read_only, bool):
        raise NarrowError("verification_sandbox.workspace_read_only must be true or false.")
    caches = config.get("dependency_caches", [])
    supported_caches = {"pip", "npm", "pnpm", "yarn"}
    if not isinstance(caches, list) or len(caches) > 4 or any(
        not isinstance(item, str) or item not in supported_caches for item in caches
    ) or len(set(caches)) != len(caches):
        raise NarrowError("verification_sandbox.dependency_caches must be a unique list of pip, npm, pnpm, and/or yarn.")
    return mode, image, memory, cpus, pids, read_only, caches

def prepare_sandbox_command(
    command: list[str], cwd: Path, config: dict[str, Any], *,
    cache_root: Path | None = None, cache_scope: str | None = None,
) -> SandboxPlan | None:
    """Build a fail-closed local container command; never pulls an image."""
    mode, image, memory, cpus, pids, read_only, dependency_caches = _validated_options(config)
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
    mount_spec = f"type=bind,src={workspace_text},dst=/workspace"
    if read_only:
        mount_spec += ",readonly"
    argv = [
        engine_path, "run", "--rm", "--name", container_name,
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit", str(pids), "--memory", memory, "--cpus", cpus,
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--mount", mount_spec,
        "--workdir", "/workspace", "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp", "--env", "XDG_CACHE_HOME=/tmp/.cache",
    ]
    if os.name != "nt" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        argv.extend(["--user", f"{os.getuid()}:{os.getgid()}"])

    if dependency_caches:
        if cache_root is None or not isinstance(cache_scope, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cache_scope):
            raise NarrowError("Configured dependency caches require a private NARROW cache root and valid task scope.")
        base = Path(cache_root).expanduser()
        if base.is_symlink():
            raise NarrowError("NARROW container cache root must not be a symbolic link.")
        try:
            base.mkdir(parents=True, mode=0o700, exist_ok=True)
            if base.is_symlink() or not base.is_dir():
                raise NarrowError("NARROW container cache root is not a regular directory.")
            scoped = base / cache_scope
            scoped.mkdir(mode=0o700, exist_ok=True)
            if scoped.is_symlink() or not scoped.is_dir():
                raise NarrowError("NARROW task dependency cache directory is redirected.")
        except OSError as exc:
            raise NarrowError(f"Could not prepare the private dependency cache: {exc}") from exc
        cache_locations = {
            "pip": ("/narrow-cache/pip", "PIP_CACHE_DIR"),
            "npm": ("/narrow-cache/npm", "npm_config_cache"),
            "pnpm": ("/narrow-cache/pnpm", "npm_config_store_dir"),
            "yarn": ("/narrow-cache/yarn", "YARN_CACHE_FOLDER"),
        }
        for name in dependency_caches:
            host_cache = scoped / name
            host_cache.mkdir(mode=0o700, exist_ok=True)
            if host_cache.is_symlink() or not host_cache.is_dir():
                raise NarrowError(f"Dependency cache '{name}' is redirected or is not a directory.")
            try:
                resolved_cache = host_cache.resolve(strict=True)
                if not resolved_cache.is_relative_to(scoped.resolve(strict=True)):
                    raise NarrowError(f"Dependency cache '{name}' escapes its task-specific cache root.")
            except OSError as exc:
                raise NarrowError(f"Could not validate dependency cache '{name}': {exc}") from exc
            container_path, env_name = cache_locations[name]
            host_text = str(resolved_cache).replace("\\", "/") if os.name == "nt" else str(resolved_cache)
            argv.extend(["--mount", f"type=bind,src={host_text},dst={container_path},rw"])
            argv.extend(["--env", f"{env_name}={container_path}"])

    argv.extend(["--entrypoint", command[0], image, *command[1:]])
    return SandboxPlan(argv, engine_path, container_name)

def cleanup_container(engine: str, container_name: str) -> None:
    """Best-effort cleanup after timeout or interruption."""
    try:
        subprocess.run([engine, "rm", "--force", container_name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
