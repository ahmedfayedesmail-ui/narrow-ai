from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .locks import inherited_lock_options, task_execution_lock
from .sandbox import cleanup_container, prepare_sandbox_command
from .windows_job import assign_process_to_job

from .core import (
    NarrowError,
    Store,
    changed_paths,
    classify_paths,
    unsafe_workspace_links,
    git_status,
    load_config,
    make_handoff,
    render_task_brief,
    redact,
    run_git,
    safe_git_environment,
    task_workspace,
    write_internal_file,
)


BLOCKED_FLAGS = {
    "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox",
    "--allow-dangerously-skip-permissions",
    "--yolo",
    "--full-auto",
}


def _stop_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
        else:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _helper_file_hashes(workspace: Path) -> dict[str, str]:
    """Fingerprint NARROW-owned instructions that must remain immutable during an agent run."""
    result: dict[str, str] = {}
    for name in ("NARROW_TASK.md", "NARROW_HANDOFF.md", "NARROW_PROMPT.md"):
        path = workspace / name
        if path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[name] = digest.hexdigest()
        elif path.exists() or path.is_symlink():
            # Treat links/special files as a distinct unsafe state without reading them.
            result[name] = "UNSAFE_NON_REGULAR_OR_LINKED_FILE"
    return result


def run_verification_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    output_limit: int = 64 * 1024,
    _lock_fd: int | None = None,
    sandbox: dict[str, Any] | None = None,
    sandbox_cache_root: Path | None = None,
    sandbox_cache_scope: str | None = None,
) -> tuple[int, str]:
    """Run a configured check with bounded retained output and best-effort tree cleanup.

    Output is drained continuously so a verbose test cannot deadlock on a full pipe.
    Only the final `output_limit` bytes are retained for the local evidence journal.
    """
    creationflags = 0
    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_options["start_new_session"] = True

    check_env = dict(env)
    for key in tuple(check_env):
        if key.startswith("GIT_"):
            check_env.pop(key, None)
    check_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
    sandbox_plan = prepare_sandbox_command(
        command, cwd, sandbox or {"mode": "off"},
        cache_root=sandbox_cache_root, cache_scope=sandbox_cache_scope,
    )
    execution_command = sandbox_plan.command if sandbox_plan else command
    try:
        with inherited_lock_options(_lock_fd) as lock_options:
            process = subprocess.Popen(
                execution_command,
                cwd=cwd,
                env=check_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=creationflags,
                **popen_options,
                **lock_options,
            )
    except FileNotFoundError as exc:
        raise NarrowError(f"Executable for verification check not found: {command[0]}") from exc
    except OSError as exc:
        raise NarrowError(f"Could not start verification check {command[0]}: {exc}") from exc

    process_job = assign_process_to_job(process)

    captured = bytearray()
    capture_lock = threading.Lock()

    def drain_output() -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            while True:
                chunk = os.read(stream.fileno(), 8192)
                if not chunk:
                    break
                with capture_lock:
                    captured.extend(chunk)
                    if len(captured) > output_limit:
                        del captured[: len(captured) - output_limit]
        except (OSError, ValueError):
            # The parent closes the pipe after process cleanup; partial output is retained.
            pass

    reader = threading.Thread(target=drain_output, name="narrow-check-output", daemon=True)
    reader.start()
    exit_code: int
    try:
        exit_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_process_tree(process)
        if sandbox_plan is not None:
            cleanup_container(sandbox_plan.engine, sandbox_plan.container_name)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        exit_code = 124
    except KeyboardInterrupt:
        _stop_process_tree(process)
        if sandbox_plan is not None:
            cleanup_container(sandbox_plan.engine, sandbox_plan.container_name)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        raise
    finally:
        reader.join(timeout=3)
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
        if process_job is not None:
            process_job.close()

    with capture_lock:
        output = bytes(captured).decode("utf-8", errors="replace")
    if exit_code == 124:
        output += f"\n[Timed out after {timeout} seconds; the process group was asked to stop.]"
    return exit_code, output


def run_agent(
    store: Store,
    task_id: str,
    command: list[str],
    *,
    resume: bool = False,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    stdin_text: str | None = None,
    _lock_fd: int | None = None,
) -> int:
    if _lock_fd is None:
        with task_execution_lock(store.state_dir, task_id) as acquired_fd:
            return run_agent(store, task_id, command, resume=resume, event_callback=event_callback, stdin_text=stdin_text, _lock_fd=acquired_fd)
    if not command:
        raise NarrowError("Pass an agent command after `--`, for example: narrow run -- claude")
    if any(not isinstance(part, str) or not part or "\x00" in part for part in command):
        raise NarrowError("Agent commands must be non-empty argument strings without NUL characters.")
    stdin_bytes: bytes | None = None
    if stdin_text is not None:
        if not isinstance(stdin_text, str) or "\x00" in stdin_text:
            raise NarrowError("Provider prompt input must be valid text without NUL characters.")
        stdin_bytes = stdin_text.encode("utf-8")
        if len(stdin_bytes) > 4 * 1024 * 1024:
            raise NarrowError("Provider task prompt exceeds NARROW's 4 MiB safety limit. Shorten the task/handoff before launching the provider.")
    blocked = sorted({arg for arg in command for flag in BLOCKED_FLAGS if arg == flag or arg.startswith(flag + "=")})
    if blocked:
        raise NarrowError(
            "NARROW refuses known permission-bypass flags: " + ", ".join(blocked) +
            ". Keep the agent's own approval and safety controls enabled."
        )

    task = store.get_task(task_id)
    root = store.root
    worktree = task_workspace(task)
    current_head = run_git(worktree, ["rev-parse", "HEAD"])
    current_branch = run_git(worktree, ["branch", "--show-current"], check=False)
    if current_head != task["base_sha"] or current_branch != task["branch"]:
        raise NarrowError("The task worktree no longer matches its original branch and base commit. Inspect it manually; NARROW will not run an agent here.")

    # The database is the source of truth for the task contract. Restore any
    # agent-edited helper before another run, never following a symlink or
    # writing through a hard link.
    task_brief = worktree / "NARROW_TASK.md"
    if task_brief.is_dir() and not task_brief.is_symlink():
        raise NarrowError("NARROW_TASK.md is a directory. Inspect the isolated workspace manually.")
    canonical_brief = render_task_brief(task)
    try:
        brief_matches = (
            task_brief.is_file()
            and not task_brief.is_symlink()
            and task_brief.stat().st_nlink == 1
            and task_brief.read_text(encoding="utf-8") == canonical_brief
        )
    except (OSError, UnicodeError):
        brief_matches = False
    if not brief_matches:
        write_internal_file(worktree, "NARROW_TASK.md", canonical_brief)
        store.add_event(task_id, "TASK_BRIEF_RESTORED", {"reason": "The task brief differed from the authoritative local task record."})
        print("NARROW restored its task brief from the local task record before launch.")

    original_head = run_git(root, ["rev-parse", "HEAD"])
    original_status = git_status(root)
    if original_head != task["base_sha"] or original_status != task["base_status"]:
        raise NarrowError("The original checkout changed since this task started. NARROW will not launch an agent until you review the original checkout.")

    config = load_config(root)
    try:
        raw_interval = config.get("monitor_interval_seconds", 2)
        raw_max_seconds = config.get("max_run_seconds", 3600)
        raw_max_files = config.get("max_changed_files", 100)
        if isinstance(raw_interval, bool) or not isinstance(raw_interval, (int, float)) or not math.isfinite(float(raw_interval)):
            raise ValueError("monitor_interval_seconds must be a finite number")
        if (not isinstance(raw_max_seconds, int) or isinstance(raw_max_seconds, bool)
                or not isinstance(raw_max_files, int) or isinstance(raw_max_files, bool)):
            raise ValueError("run and file limits must be integers")
        parsed_max_seconds = raw_max_seconds
        parsed_max_files = raw_max_files
        if parsed_max_seconds < 1 or parsed_max_files < 1:
            raise ValueError("run and file limits must be positive")
    except (TypeError, ValueError, OverflowError) as exc:
        raise NarrowError("Invalid safety limits in .narrow/config.json. Use a finite monitor interval and positive integer run/file limits.") from exc
    interval = max(0.5, min(float(raw_interval), 10.0))
    max_seconds = max(30, min(parsed_max_seconds, 24 * 60 * 60))
    max_files = max(1, parsed_max_files)
    if config.get("stop_on_scope_violation", True) is not True:
        raise NarrowError("stop_on_scope_violation must remain true. NARROW does not allow automatic runs to ignore scope violations.")

    # Never resume work from a workspace that already contains a scope violation.
    preflight_paths = changed_paths(worktree)
    preflight_scope = classify_paths(preflight_paths, task["allowed"], task["denied"])
    preflight_violations = preflight_scope["outside"] + preflight_scope["denied"]
    if preflight_violations:
        raise NarrowError(
            "The task workspace already contains out-of-scope changes. Review/remove them before resuming: "
            + ", ".join(preflight_violations[:20])
        )
    if len(preflight_paths) > max_files:
        raise NarrowError(
            f"The task workspace already exceeds the configured changed-file limit ({len(preflight_paths)} > {max_files})."
        )

    env = safe_git_environment()
    handoff_path = worktree / "NARROW_HANDOFF.md"
    if resume:
        write_internal_file(worktree, "NARROW_HANDOFF.md", make_handoff(store, task_id))
    escaped_links = unsafe_workspace_links(worktree)
    if escaped_links:
        raise NarrowError(
            "The task copy contains unsafe symlinks or hard-linked files. NARROW will not launch an agent because writes through them could affect external files: "
            + ", ".join(escaped_links[:20])
        )
    expected_helper_hashes = _helper_file_hashes(worktree)
    env.update({
        "NARROW_PROJECT_ROOT": str(root),
        "NARROW_TASK_ID": task_id,
        "NARROW_TASK_FILE": str(worktree / "NARROW_TASK.md"),
        "NARROW_HANDOFF_FILE": str(handoff_path) if resume else "",
        "NARROW_PROMPT_FILE": str(worktree / "NARROW_PROMPT.md"),
        "NARROW_WORKTREE": str(worktree),
    })
    creationflags = 0
    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_options["start_new_session"] = True

    print("NARROW isolated run")
    print(f"Task: {task['title']} ({task_id})")
    print(f"Isolated workspace: {worktree}")
    print("The agent inherits your user permissions. An isolated repository copy is not an OS sandbox.")
    print("Task brief: NARROW_TASK.md — ask your agent to read it before editing.")
    print("Safety note: the isolated copy is not a machine-level sandbox; agent commands keep your user permissions.")
    if resume:
        print("Continuity brief: NARROW_HANDOFF.md — includes recorded decisions, progress, changes, and checks.")
        print("Ask the agent to resume from that handoff without repeating completed work.\n")
    else:
        print("\n")
    # Do not persist command arguments: provider prompts and user-supplied argv
    # can contain private project context or credentials.
    store.add_event(task_id, "AGENT_RUN_STARTED", {
        "executable": Path(command[0]).name,
        "argument_count": len(command),
        "provider_managed": event_callback is not None,
        "resume": resume,
    })

    launch_options: dict[str, Any] = {}
    if event_callback is not None:
        # Provider JSONL mode is non-interactive; generic commands retain the
        # original terminal so tools that need a TTY continue to work.
        launch_options.update({
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "bufsize": 0,
        })
    try:
        with inherited_lock_options(_lock_fd) as lock_options:
            process = subprocess.Popen(
                command,
                cwd=worktree,
                env=env,
                stdin=subprocess.PIPE if stdin_bytes is not None else None,
                shell=False,
                creationflags=creationflags,
                **launch_options,
                **popen_options,
                **lock_options,
            )
    except FileNotFoundError as exc:
        raise NarrowError(f"Agent executable not found: {command[0]}. Check that it is installed and on PATH.") from exc
    except OSError as exc:
        raise NarrowError(f"Could not start agent: {exc}") from exc

    process_job = assign_process_to_job(process)

    callback_errors: list[str] = []

    if stdin_bytes is not None:
        store.add_event(task_id, "AGENT_PROMPT_STDIN", {"byte_length": len(stdin_bytes)})

    def write_agent_stdin() -> None:
        stream = process.stdin
        if stream is None or stdin_bytes is None:
            return
        try:
            view = memoryview(stdin_bytes)
            offset = 0
            while offset < len(view):
                offset += stream.write(view[offset:offset + 65536])
            stream.flush()
        except (BrokenPipeError, OSError):
            # A provider may reject input during startup and exit early. The
            # provider's exit code and structured diagnostics determine outcome.
            pass
        except Exception as exc:
            callback_errors.append(f"Could not send provider prompt: {type(exc).__name__}")
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def drain_agent_output() -> None:
        stream = process.stdout
        if stream is None:
            return
        while True:
            try:
                raw_line = stream.readline(1024 * 1024 + 1)
            except (OSError, ValueError):
                return
            if not raw_line:
                return
            if len(raw_line) > 1024 * 1024:
                # Drop all oversized records, including records which happen
                # to contain a newline in the first 1 MiB + 1 bytes.
                if not raw_line.endswith(b"\n"):
                    while True:
                        remainder = stream.readline(65536)
                        if not remainder or remainder.endswith(b"\n"):
                            break
                try:
                    store.add_event(task_id, "AGENT_OUTPUT_RECORD_DROPPED", {
                        "reason": "provider output record exceeded 1 MiB",
                    })
                except Exception as exc:
                    callback_errors.append(f"Could not persist oversized-output notice: {type(exc).__name__}")
                    return
                continue
            text_line = raw_line.decode("utf-8", errors="replace")
            if event_callback is not None:
                try:
                    stripped = text_line.strip()
                    parsed = json.loads(stripped) if stripped.startswith("{") else None
                    if isinstance(parsed, dict):
                        event_callback(parsed)
                    elif text_line:
                        # Some providers still emit human-readable diagnostics
                        # to stdout/stderr. Show them, but do not persist them.
                        print(redact(text_line), end="", flush=True)
                except (json.JSONDecodeError, UnicodeError):
                    print(redact(text_line), end="", flush=True)
                except Exception as exc:
                    callback_errors.append(f"Could not persist provider event: {type(exc).__name__}")
                    return
            else:
                print(text_line, end="", flush=True)

    output_reader: threading.Thread | None = None
    if event_callback is not None:
        output_reader = threading.Thread(target=drain_agent_output, name="narrow-agent-output", daemon=True)
        output_reader.start()
    input_writer: threading.Thread | None = None
    if stdin_bytes is not None:
        input_writer = threading.Thread(target=write_agent_stdin, name="narrow-agent-input", daemon=True)
        input_writer.start()
    started = time.monotonic()
    stop_reason: str | None = None
    last_paths: list[str] = []
    try:
        while process.poll() is None:
            if callback_errors:
                stop_reason = callback_errors[0] + ". NARROW stopped rather than losing continuity evidence."
                break
            time.sleep(interval)
            elapsed = time.monotonic() - started
            if elapsed > max_seconds:
                stop_reason = f"Agent exceeded the configured {max_seconds}-second run limit."
                break

            try:
                head_now = run_git(root, ["rev-parse", "HEAD"])
                status_now = git_status(root)
                if head_now != original_head or status_now != original_status:
                    stop_reason = "The original checkout changed while the isolated agent was running."
                    store.add_event(task_id, "BASE_CHECKOUT_CHANGED", {"head_changed": head_now != original_head, "status": status_now})
                    break

                worktree_head = run_git(worktree, ["rev-parse", "HEAD"])
                if worktree_head != task["base_sha"]:
                    stop_reason = "The agent created a commit or changed HEAD. NARROW stopped the run; inspect the isolated branch manually."
                    store.add_event(task_id, "AGENT_CHANGED_HEAD", {"base_sha": task["base_sha"], "current_sha": worktree_head})
                    break

                current_helper_hashes = _helper_file_hashes(worktree)
                if current_helper_hashes != expected_helper_hashes:
                    stop_reason = "The agent modified or removed a NARROW-owned task, handoff, or prompt file."
                    store.add_event(task_id, "HELPER_FILE_CHANGED", {
                        "expected": expected_helper_hashes,
                        "actual": current_helper_hashes,
                    })
                    break

                paths = changed_paths(worktree)
                escaped_links = unsafe_workspace_links(worktree, paths)
                if escaped_links:
                    stop_reason = "The agent created an unsafe symlink or hard-linked file: " + ", ".join(escaped_links[:20])
                    store.add_event(task_id, "UNSAFE_WORKSPACE_LINK_DETECTED", {"paths": escaped_links})
                    break
                last_paths = paths
                scope = classify_paths(paths, task["allowed"], task["denied"])
                violations = scope["denied"] + scope["outside"]
                if violations:
                    stop_reason = "The agent changed files outside the allowed scope: " + ", ".join(violations[:20])
                    store.add_event(task_id, "SCOPE_VIOLATION", {"outside": scope["outside"], "denied": scope["denied"]})
                    break
                if len(paths) > max_files:
                    stop_reason = f"The agent changed more than the configured limit of {max_files} files."
                    store.add_event(task_id, "CHANGE_LIMIT_REACHED", {"count": len(paths), "limit": max_files})
                    break
            except NarrowError as exc:
                stop_reason = f"NARROW monitor could not verify repository state: {exc}"
                break
    except KeyboardInterrupt:
        _stop_process_tree(process)
        if output_reader is not None:
            output_reader.join(timeout=3)
        if input_writer is not None:
            input_writer.join(timeout=3)
        if process.stdout is not None:
            process.stdout.close()
        store.add_event(task_id, "AGENT_RUN_INTERRUPTED", {"reason": "User interrupted the supervised run."})
        store.update_status(task_id, "NEEDS_REVIEW")
        if process_job is not None:
            process_job.close()
        print("\nNARROW stopped the agent and preserved the isolated worktree and task journal.")
        return 130

    if stop_reason:
        _stop_process_tree(process)
        if output_reader is not None:
            output_reader.join(timeout=3)
        if input_writer is not None:
            input_writer.join(timeout=3)
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
        store.add_event(task_id, "AGENT_RUN_STOPPED", {"reason": stop_reason, "changed_paths": last_paths})
        store.update_status(task_id, "NEEDS_REVIEW")
        if process_job is not None:
            process_job.close()
        print(f"\nNARROW stopped the agent: {stop_reason}")
        return 125

    exit_code = process.wait()
    if output_reader is not None:
        output_reader.join(timeout=3)
    if input_writer is not None:
        input_writer.join(timeout=3)
    if process.stdout is not None:
        try:
            process.stdout.close()
        except OSError:
            pass
    if process_job is not None:
        process_job.close()
    if callback_errors:
        store.update_status(task_id, "NEEDS_REVIEW")
        store.add_event(task_id, "AGENT_OUTPUT_CAPTURE_FAILED", {"reason": callback_errors[0]})
        print("\nNARROW could not reliably record provider events. The task needs manual review.")
        return 125
    final_paths = changed_paths(worktree)
    final_scope = classify_paths(final_paths, task["allowed"], task["denied"])
    final_file_limit_exceeded = len(final_paths) > max_files
    final_head = run_git(worktree, ["rev-parse", "HEAD"], check=False)
    final_branch = run_git(worktree, ["branch", "--show-current"], check=False)
    final_helper_hashes = _helper_file_hashes(worktree)
    helper_files_changed = final_helper_hashes != expected_helper_hashes
    final_unsafe_workspace_links = unsafe_workspace_links(worktree)
    base_changed = (
        run_git(root, ["rev-parse", "HEAD"], check=False) != original_head
        or git_status(root) != original_status
    )
    if final_scope["outside"] or final_scope["denied"] or final_file_limit_exceeded or final_head != task["base_sha"] or final_branch != task["branch"] or base_changed or helper_files_changed or final_unsafe_workspace_links:
        store.add_event(task_id, "POST_RUN_SAFETY_REVIEW", {
            "outside": final_scope["outside"],
            "denied": final_scope["denied"],
            "changed_file_count": len(final_paths),
            "changed_file_limit_exceeded": final_file_limit_exceeded,
            "task_head_changed": final_head != task["base_sha"],
            "task_branch_changed": final_branch != task["branch"],
            "base_checkout_changed": base_changed,
            "narrow_helper_files_changed": helper_files_changed,
            "unsafe_workspace_links": final_unsafe_workspace_links,
        })
        store.update_status(task_id, "NEEDS_REVIEW")
        print("\nNARROW: the agent exited, but the final safety scan found a boundary violation.")
        print("Review `narrow inspect` and the task worktree before doing anything else.")
        return 125
    store.add_event(task_id, "AGENT_RUN_FINISHED", {"exit_code": exit_code, "changed_paths": final_paths})
    print(f"\nAgent exited with code {exit_code}. NARROW has not marked the task complete.")
    print("Next: `narrow inspect`, record decisions/notes, run configured checks, confirm acceptance, then `narrow finish`.")
    return exit_code
