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

from .container_lifecycle import cleanup_execution_agent_containers
from .core import NarrowError
from .policy_manifest import _validate_execution_id

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
        "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
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
    state_dir: Path,
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
    if not isinstance(stdin_text, str) or "\x00" in stdin_text:
        raise NarrowError("Docker agent prompt must be text without NUL characters.")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 24 * 60 * 60:
        raise NarrowError("Docker agent timeout must be from 1 second to 24 hours.")
    if not isinstance(output_limit, int) or isinstance(output_limit, bool) or not 1024 <= output_limit <= 1024 * 1024:
        raise NarrowError("Docker agent output limit must be from 1 KiB to 1 MiB.")

    execution_dir = _private_execution_dir(state_dir, task_id, execution_id)
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
                chunk = stream.read(8192)
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
