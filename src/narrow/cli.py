from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .core import (
    NarrowError,
    Store,
    default_config,
    accept_criterion,
    changed_paths,
    classify_paths,
    create_task,
    finish_task,
    find_repo_root,
    git_status,
    init_project,
    load_config,
    make_handoff,
    now_iso,
    record_note,
    redact,
    run_git,
    save_check_run,
    snapshot_hash,
    unsafe_workspace_links,
    task_workspace,
    write_internal_file,
    cache_root,
)
from .supervisor import run_agent, run_verification_command
from .drive import drive_task
from .hooks import run_hook_cli
from .providers import provider_status
from .discovery import discover_project, propose_contract
from .profiles import build_setup_plan, build_security_posture, profile_scope_defaults
from .dashboard import run_dashboard
from .locks import task_execution_lock
from .sandbox import prepare_sandbox_command
from .container_recovery import recover_agent_execution


def _find_root() -> Path:
    current_repo = find_repo_root()
    state = current_repo / ".narrow" / "state.sqlite3"
    if state.exists():
        return current_repo
    raise NarrowError("NARROW is not initialized in this repository. Run commands from the original checkout after `narrow init`.")


def _store() -> Store:
    return Store(_find_root())


def _task_label(task: dict[str, Any]) -> str:
    return f"{task['title']} [{task['id']}] — {task['status']}"


def cmd_init(_: argparse.Namespace) -> int:
    root = find_repo_root()
    path = init_project(root)
    print(f"Initialized NARROW in {root}")
    print(f"Config: {path}")
    print("No agent was launched, no network request was made, and no tracked project file was changed.")
    print("Edit .narrow/config.json to add project-specific, non-destructive verification commands.")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    root = find_repo_root()
    title = args.title
    allowed = list(args.allow)
    denied = list(args.deny)
    acceptance = list(args.accept)

    proposal = None
    if args.ollama and not args.auto_contract:
        raise NarrowError("--ollama requires --auto-contract.")
    if args.auto_contract:
        proposal = propose_contract(
            root,
            args.goal,
            ollama_model=args.model if args.ollama else None,
        )
        saved_config = load_config(root)
        profile_allow, profile_deny = profile_scope_defaults(root, saved_config.get("project_profile"))
        title = title or proposal["title"]
        allowed = allowed or profile_allow or proposal["allow"]
        denied = denied or profile_deny or proposal["deny"]
        acceptance = acceptance or proposal["accept"]
        print("NARROW generated a task proposal. Review every path and criterion:")
        print("Title: " + title)
        print("Goal: " + args.goal)
        print("Allowed: " + (", ".join(allowed) or "(none)"))
        print("Denied: " + (", ".join(denied) or "(none)"))
        for index, item in enumerate(acceptance, 1):
            print(f"  {index}. {item}")
        print("Suggested checks:")
        for check in proposal["checks"]:
            print(f"  {check['name']}: {shlex.join(check['command'])}")
        print("No task has been created yet.")
        try:
            answer = input("Create this task with the proposed contract? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("Cancelled. No task was created.")
            return 1

    if not title:
        raise NarrowError("Provide --title or use --auto-contract.")
    if not allowed:
        raise NarrowError("Provide at least one --allow path or use --auto-contract.")
    if not acceptance:
        raise NarrowError("Provide at least one --accept criterion or use --auto-contract.")
    store = Store(root)
    task = create_task(store, title, args.goal, allowed, denied, acceptance)
    approved_contract = {
        "title": title,
        "goal": args.goal,
        "allow": allowed,
        "deny": denied,
        "accept": acceptance,
    }
    if proposal is not None:
        proposed_contract = {
            "title": proposal["title"],
            "goal": proposal["goal"],
            "allow": proposal["allow"],
            "deny": proposal["deny"],
            "accept": proposal["accept"],
        }
        store.add_event(task["id"], "CONTRACT_VERSION_RECORDED", {
            "version": 1, "label": "generated proposal", "source": proposal["generation"],
            "contract": proposed_contract, "changes": {},
        })
        changes = {
            key: {"from": proposed_contract[key], "to": approved_contract[key]}
            for key in approved_contract if proposed_contract[key] != approved_contract[key]
        }
        store.add_event(task["id"], "CONTRACT_VERSION_RECORDED", {
            "version": 2, "label": "human-approved contract", "source": "human-confirmed",
            "parent_version": 1, "contract": approved_contract, "changes": changes,
        })
    else:
        store.add_event(task["id"], "CONTRACT_VERSION_RECORDED", {
            "version": 1, "label": "manually defined contract", "source": "user",
            "contract": approved_contract, "changes": {},
        })
    print("Task created in an isolated repository copy.")
    print(f"Task: {_task_label(task)}")
    print(f"Isolated workspace: {task['worktree']}")
    print(f"Branch: {task['branch']}")
    print(f"Task brief: {Path(task['worktree']) / 'NARROW_TASK.md'}")
    print("\nNext: configure at least one reviewed check in .narrow/config.json, then run:")
    print("  narrow drive --provider claude")
    print("  narrow drive --provider codex")
    print("Or use a configured generic adapter with `narrow drive --agent NAME`.")
    return 0



def cmd_contract_history(args: argparse.Namespace) -> int:
    """Show the proposed and approved task-contract versions and field-level drift."""
    store = _store()
    task = store.get_task(args.task)
    with store.connect() as db:
        rows = db.execute(
            "SELECT payload_json,created_at FROM events WHERE task_id=? AND event_type='CONTRACT_VERSION_RECORDED' ORDER BY id",
            (task["id"],),
        ).fetchall()
    versions = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        item = dict(payload)
        item["created_at"] = row["created_at"]
        versions.append(item)
    if args.json:
        print(json.dumps({"schema": "narrow.contract-history.v1", "task_id": task["id"], "versions": versions}, ensure_ascii=False, indent=2))
        return 0
    print(f"Contract history — {task['title']} [{task['id']}]")
    if not versions:
        print("No versioned contract records exist for this task.")
        return 0
    for item in versions:
        print(f"\nVersion {item.get('version')}: {item.get('label')} ({item.get('created_at')})")
        if item.get("changes"):
            for field, change in item["changes"].items():
                print(f"  {field}: {json.dumps(change.get('from'), ensure_ascii=False)} -> {json.dumps(change.get('to'), ensure_ascii=False)}")
        else:
            print("  Initial contract")
    print("\nThe approved task contract remains immutable for this task. Create a new task to change its scope.")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Prepare a project profile and verification suggestions without running commands."""
    if args.json and args.apply:
        raise NarrowError("--json is read-only; use the interactive --apply mode separately.")
    root = find_repo_root()
    discovery = discover_project(root)
    plan = build_setup_plan(root, discovery, args.profile)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    print(f"NARROW setup — {root}")
    print(f"Profile: {plan['profile']} — {plan['profile_description']}")
    print("Suggested allowed paths: " + (", ".join(plan["allowed_paths"]) or "(none detected; define a narrow allowlist manually)"))
    print("Suggested denied paths: " + (", ".join(plan["denied_paths"]) or "(none detected)"))
    print("Suggested verification commands (not executed):")
    if plan["checks"]:
        for item in plan["checks"]:
            print(f"  {item['name']}: {shlex.join(item['command'])} ({item['timeout_seconds']}s)")
    else:
        print("  (no recognizable test command found)")
    print("\nSecurity boundary:")
    print("  Git task copies are not OS sandboxes.")
    print("  This setup does not launch or isolate an agent.")
    print("  Verification sandbox stays OFF unless configured separately.")
    print("  Human review remains required.")

    if not args.apply:
        print("\nRead-only plan. Nothing was written and no commands were run.")
        print("Review this plan, then run narrow setup --apply to save the profile and add missing check suggestions.")
        return 0

    init_project(root)
    config = load_config(root)
    existing = config.setdefault("checks", {})
    additions: dict[str, dict[str, Any]] = {}
    conflicts: list[str] = []
    for item in plan["checks"]:
        spec = {"command": item["command"], "timeout_seconds": item["timeout_seconds"]}
        current = existing.get(item["name"])
        if current is None:
            additions[item["name"]] = spec
        elif not isinstance(current, dict) or current.get("command") != spec["command"]:
            conflicts.append(item["name"])

    profile_record = {
        "name": plan["profile"],
        "allowed_paths": plan["allowed_paths"],
        "denied_paths": plan["denied_paths"],
    }
    profile_changed = config.get("project_profile") != profile_record
    if not additions and not profile_changed:
        print("\nNo changes needed. Existing check configuration was preserved.")
        if conflicts:
            print("Existing checks with different commands were left unchanged: " + ", ".join(conflicts))
        return 0

    print("\nChanges proposed:")
    print("  Add checks: " + (", ".join(additions) if additions else "(none)"))
    print("  Save project profile: " + plan["profile"] if profile_changed else "  Keep existing project profile")
    if conflicts:
        print("  Preserve conflicting existing checks: " + ", ".join(conflicts))
    print("No suggested command will be executed.")
    try:
        answer = input("Apply these setup changes? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        print("Cancelled. No setup changes were saved.")
        return 1

    existing.update(additions)
    config["project_profile"] = profile_record
    write_internal_file(root / ".narrow", "config.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    print(f"Saved project profile '{plan['profile']}' and added {len(additions)} check suggestion(s) to .narrow/config.json.")
    print("Review every command before running narrow verify or narrow drive.")
    return 0


def cmd_security(args: argparse.Namespace) -> int:
    """Show the effective separation between workflow coordination and OS enforcement."""
    root = find_repo_root()
    path = root / ".narrow" / "config.json"
    initialized = path.is_file()
    config = load_config(root) if initialized else default_config()
    report = build_security_posture(config, initialized=initialized)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("NARROW security posture")
    print(f"Project profile: {report['project_profile'] or '(not selected)'}")
    print("\nOrchestration layer")
    for name, detail in report["orchestration"].items():
        print(f"  {name.replace('_', ' ').title()}: {detail}")
    print("\nOS enforcement layer")
    for name, detail in report["security_enforcement"].items():
        print(f"  {name.replace('_', ' ').title()}: {detail}")
    print("\nImportant: orchestration is not a sandbox. Use narrow security --json for automation.")
    print("Read-only: no credentials are inspected, no network is contacted, and no processes are launched.")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Inspect project manifests and optionally add discovered checks to config."""
    root = find_repo_root()
    result = discover_project(root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"NARROW project discovery — {root}")
        print("Languages: " + (", ".join(result["languages"]) or "not identified"))
        print("Test frameworks: " + (", ".join(result["frameworks"]) or "not identified"))
        print("Suggested checks:")
        for item in result["checks"]:
            print(f"  {item['name']}: {shlex.join(item['command'])}")
            print(f"    {item['reason']}")
        print("Suggested allowed paths: " + (", ".join(result["suggested_allow"]) or "none; define a narrow allowlist manually"))
        print("Suggested denied paths: " + ", ".join(result["suggested_deny"]))
        if result["scan_limit_reached"]:
            print("Warning: file scan limit reached; discovery may be incomplete.")
    if not args.apply:
        print("Read-only discovery. No commands were run and no config was changed.")
        return 0
    if not result["checks"]:
        print("No recognizable test command was found; configuration was not changed.")
        return 0
    init_project(root)
    config = load_config(root)
    existing = config.setdefault("checks", {})
    additions = {item["name"]: {key: item[key] for key in ("command", "timeout_seconds")} for item in result["checks"] if item["name"] not in existing}
    if not additions:
        print("No changes: every discovered check already has a configured entry.")
        return 0
    print("The following commands will be added to .narrow/config.json:")
    for name, spec in additions.items():
        print(f"  {name}: {shlex.join(spec['command'])}")
    try:
        answer = input("Add these suggestions? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        print("Cancelled. No configuration was changed.")
        return 1
    existing.update(additions)
    write_internal_file(root / ".narrow", "config.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    print("Added " + str(len(additions)) + " suggested check(s). Review each command before running narrow verify or narrow drive.")
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    """Draft a task contract. This command never starts an agent or writes files."""
    root = find_repo_root()
    model = args.model if args.ollama else None
    proposal = propose_contract(root, args.goal, ollama_model=model)
    if args.json:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
        return 0
    print("NARROW task proposal — review before using")
    print("Title: " + proposal["title"])
    print("Goal: " + proposal["goal"])
    print("Allowed paths:")
    for item in proposal["allow"]:
        print("  " + item)
    print("Denied paths:")
    for item in proposal["deny"]:
        print("  " + item)
    print("Acceptance criteria:")
    for index, item in enumerate(proposal["accept"], 1):
        print(f"  {index}. {item}")
    print("Suggested checks:")
    for item in proposal["checks"]:
        print(f"  {item['name']}: {shlex.join(item['command'])}")
    print("Generation: " + proposal["generation"])
    print(proposal["warning"])
    print("NARROW has not created the task. Review the paths and criteria, then pass them explicitly to narrow start.")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    items = provider_status(probe_versions=args.versions)
    if args.json:
        print(json.dumps({"schema": "narrow.providers.v1", "providers": items}, ensure_ascii=False, indent=2))
        return 0
    print("NARROW provider adapters (built-in and installed plugins; no credentials are read):")
    for item in items:
        state = "AVAILABLE" if item["available"] else "NOT FOUND"
        path = f" — {item['path']}" if item["path"] else ""
        version = f" — {item['version']}" if item.get("version") else ""
        issue = f" — {item['version_error']}" if item.get("version_error") else ""
        compatibility = f" — compatibility: {item['compatibility']}" if item.get("compatibility") not in {"not_checked", "not_version_gated"} else ""
        note = f" ({item['compatibility_note']})" if item.get("compatibility_note") else ""
        print(f"  {item['name']}: {state}{version}{path}{compatibility}{note}{issue}")
    print("\n`--versions` runs only the detected CLI's --version command. It does not check sign-in or inspect credential files.")
    print("Use `narrow drive --provider claude` or `--provider codex` after installing and authenticating that provider CLI.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Read-only diagnostics. Does not initialize or modify the repository."""
    print(f"NARROW doctor — {__version__}")
    print(f"Python: {platform.python_version()} ({sys.executable})")
    checks: list[tuple[str, bool, str]] = []
    git_path = shutil.which("git")
    checks.append(("Git executable", bool(git_path), git_path or "not found on PATH"))
    try:
        root = find_repo_root()
        checks.append(("Git repository", True, str(root)))
        config_path = root / ".narrow" / "config.json"
        state_path = root / ".narrow" / "state.sqlite3"
        if not config_path.is_file():
            checks.append(("NARROW initialization", False, "not initialized; run `narrow init`"))
        else:
            try:
                config = load_config(root)
                checks.append(("NARROW configuration", True, str(config_path)))
                check_specs = config.get("checks", {})
                has_checks = isinstance(check_specs, dict) and bool(check_specs)
                checks.append(("Verification checks", has_checks, f"{len(check_specs) if isinstance(check_specs, dict) else 0} configured"))
            except NarrowError as exc:
                checks.append(("NARROW configuration", False, str(exc)))
        checks.append(("Local task database", state_path.is_file(), str(state_path) if state_path.is_file() else "not created yet"))
    except NarrowError as exc:
        checks.append(("Git repository", False, str(exc)))
    for item in provider_status(probe_versions=args.versions):
        details = item.get("path") or "not found on PATH"
        if item.get("version"):
            details += f" ({item['version']})"
        if item.get("compatibility") == "upgrade_recommended":
            details += f" — upgrade recommended: {item.get('compatibility_note', 'older CLI version')}"
        checks.append((f"Provider: {item['name']}", bool(item["available"]), details))
    for name, passed, detail in checks:
        label = "OK" if passed else "ACTION NEEDED"
        print(f"[{label}] {name}: {detail}")
    print("\nDiagnostics are read-only. NARROW does not inspect provider credentials or make network requests.")
    return 0 if all(passed for name, passed, _ in checks if name in {"Git executable", "Git repository", "NARROW configuration"}) else 1


def cmd_hook(args: argparse.Namespace) -> int:
    return run_hook_cli(args.hook_args)



def cmd_tasks(args: argparse.Namespace) -> int:
    """List local tasks without exposing prompts, notes, or provider payloads."""
    store = _store()
    tasks = store.list_tasks(include_closed=args.all, limit=args.limit)
    if args.json:
        rows = [{
            "id": item["id"],
            "title": item["title"],
            "goal": item["goal"],
            "status": item["status"],
            "branch": item["branch"],
            "updated_at": item["updated_at"],
        } for item in tasks]
        print(json.dumps({"schema": "narrow.tasks.v1", "tasks": rows}, ensure_ascii=False, indent=2))
        return 0
    if not tasks:
        print("No tasks found. Start one with `narrow start`.")
        return 0
    for item in tasks:
        print(f"{item['status']:<22} {item['id']}  {item['title']}")
    print(f"\n{len(tasks)} task(s). Use `narrow status --task <id>` or `narrow dashboard` for details.")
    return 0

def cmd_status(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    print(_task_label(task))
    print(f"Goal: {task['goal']}")
    print(f"Base: {task['base_sha']}")
    print(f"Branch: {task['branch']}")
    print(f"Isolated workspace: {task['worktree']}")
    print(f"Allowed: {', '.join(task['allowed'])}")
    print(f"Denied: {', '.join(task['denied']) or '(none)'}")
    print("Acceptance:")
    for index, item in enumerate(task["acceptance"], 1):
        flag = "x" if item["accepted"] else " "
        print(f"  [{flag}] {index}. {item['text']}")
    events = store.events(task["id"], limit=10000)
    print(f"Recorded events: {len(events)}")
    return 0


def _agent_command(args: argparse.Namespace) -> list[str]:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    return command


def cmd_run(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    return run_agent(store, task["id"], _agent_command(args), resume=False)


def cmd_drive(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    return drive_task(
        store,
        task["id"],
        args.agent,
        provider=args.provider,
        model=args.model,
        max_turns=args.max_turns,
        max_attempts=args.max_attempts,
        max_budget_usd=args.max_budget_usd,
        yes_checks=args.yes_checks,
        allow_subagents=args.allow_subagents,
        effort=args.effort,
        ultracode=args.ultracode,
        container_agent=args.container_agent,
    )


def cmd_resume(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    command = _agent_command(args)
    if not command:
        print(make_handoff(store, task["id"]))
        return 0
    return run_agent(store, task["id"], command, resume=True)


def cmd_inspect(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    workspace = task_workspace(task)
    paths = changed_paths(workspace)
    result = classify_paths(paths, task["allowed"], task["denied"])
    unsafe_links = unsafe_workspace_links(workspace)
    print(f"Task: {_task_label(task)}")
    print(f"Changed files: {len(paths)}")
    if unsafe_links:
        print("\nUNSAFE WORKSPACE LINKS")
        for item in unsafe_links:
            print(f"  {item}")
    for category in ("allowed", "outside", "denied"):
        print(f"\n{category.upper()} ({len(result[category])})")
        for path in result[category]:
            print(f"  {path}")
    try:
        head = run_git(workspace, ["rev-parse", "HEAD"])
        if head != task["base_sha"]:
            print("\nWARNING: task branch HEAD changed; agent may have committed or switched state.")
    except NarrowError as exc:
        print(f"\nWARNING: could not inspect task HEAD: {exc}")
    store.add_event(task["id"], "INSPECTION", result)
    return 2 if result["outside"] or result["denied"] or unsafe_links else 0


def cmd_note(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    record_note(store, task["id"], "NOTE", args.text)
    print("Note saved to the local task journal.")
    return 0


def cmd_decision(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    record_note(store, task["id"], "DECISION", args.text)
    print("Decision saved to the local task journal.")
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    content = make_handoff(store, task["id"])
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        print(f"Handoff written to {output}")
    else:
        print(content)
    return 0


def cmd_accept(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    accept_criterion(store, task["id"], args.criterion, args.evidence)
    print(f"Acceptance criterion {args.criterion} recorded as human-confirmed.")
    return 0


def _confirm(command: list[str], yes: bool) -> bool:
    print("Verification command:")
    print("  " + shlex.join(command))
    print("This executes the configured command inside the isolated task workspace with your user permissions.")
    if yes:
        return True
    try:
        answer = input("Run this check? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def cmd_verify(args: argparse.Namespace, _lock_fd: int | None = None, _store_obj: Store | None = None, _task: dict[str, Any] | None = None) -> int:
    if _lock_fd is None:
        store_obj = _store_obj or _store()
        task_obj = _task or store_obj.get_task(args.task)
        with task_execution_lock(store_obj.state_dir, task_obj["id"]) as acquired_fd:
            return cmd_verify(args, _lock_fd=acquired_fd, _store_obj=store_obj, _task=task_obj)
    store = _store_obj or _store()
    task = _task or store.get_task(args.task)
    config = load_config(store.root)
    sandbox_config = config.get("verification_sandbox", {"mode": "off"})
    if not isinstance(sandbox_config, dict):
        raise NarrowError("verification_sandbox must be a JSON object in .narrow/config.json.")
    checks = config.get("checks", {})
    if args.check not in checks:
        available = ", ".join(checks) or "(none configured)"
        raise NarrowError(f"Unknown check '{args.check}'. Configured checks: {available}. Edit .narrow/config.json first.")
    spec = checks[args.check]
    if not isinstance(spec, dict):
        raise NarrowError(f"Check '{args.check}' configuration must be an object.")
    command = spec.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part and "\x00" not in part for part in command):
        raise NarrowError(f"Check '{args.check}' must define a non-empty argv array. Shell strings are not supported.")
    workspace = task_workspace(task)
    # Validate the configured container engine and local image before asking
    # the user to approve this check.
    prepare_sandbox_command(
        command, workspace, sandbox_config,
        cache_root=cache_root() / "container-caches", cache_scope=task["id"],
    )
    unsafe_before = unsafe_workspace_links(workspace)
    if unsafe_before:
        raise NarrowError("NARROW refuses to run verification while unsafe symlinks or hard-linked files exist: " + ", ".join(unsafe_before[:20]))
    if sandbox_config.get("mode", "off") == "off":
        print("Verification will run with your user permissions inside the isolated task worktree.")
    else:
        print("Verification will run in the configured " + str(sandbox_config.get("mode")) + " container profile.")
    if not _confirm(command, args.yes):
        print("Cancelled. No verification command was run.")
        return 1
    before = snapshot_hash(workspace)
    try:
        raw_timeout = spec.get("timeout_seconds", 300)
        if not isinstance(raw_timeout, int) or isinstance(raw_timeout, bool) or raw_timeout < 1:
            raise ValueError("timeout_seconds must be a positive integer")
        timeout = min(raw_timeout, 24 * 60 * 60)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NarrowError(f"Check '{args.check}' has an invalid timeout_seconds value.") from exc
    started = time.monotonic()
    verify_env = os.environ.copy()
    verify_env["PYTHONDONTWRITEBYTECODE"] = "1"
    exit_code, output = run_verification_command(
        command, cwd=workspace, env=verify_env, timeout=timeout, _lock_fd=_lock_fd,
        sandbox=sandbox_config,
        sandbox_cache_root=cache_root() / "container-caches",
        sandbox_cache_scope=task["id"],
    )
    duration = int((time.monotonic() - started) * 1000)
    after = snapshot_hash(workspace)
    unsafe_after = unsafe_workspace_links(workspace)
    if unsafe_after:
        exit_code = 125
        output += "\nNARROW: verification created unsafe symlinks or hard-linked files: " + ", ".join(unsafe_after[:20])
    if before != after and exit_code == 0:
        exit_code = 125
        output += "\nNARROW: verification changed the workspace. The result is not accepted; restore generated artifacts or add them to Git ignore, then rerun."
    save_check_run(store, task["id"], args.check, command, after, exit_code, duration, output)
    print((redact(output) or "(no output)")[-6000:])
    print(f"\nCheck {'PASSED' if exit_code == 0 else 'FAILED'} (exit {exit_code}; {duration} ms).")
    return 0 if exit_code == 0 else 1



def _work_receipt(store: Store, task: dict[str, Any]) -> dict[str, Any]:
    workspace = task_workspace(task)
    paths = changed_paths(workspace)
    scope = classify_paths(paths, task["allowed"], task["denied"])
    unsafe_links = unsafe_workspace_links(workspace)
    snapshot = None if unsafe_links else snapshot_hash(workspace)
    with store.connect() as db:
        check_rows = db.execute(
            "SELECT check_name,snapshot_hash,exit_code,passed,duration_ms,created_at FROM check_runs WHERE task_id=? ORDER BY id DESC LIMIT 100",
            (task["id"],),
        ).fetchall()
    checks = []
    latest_by_name: set[str] = set()
    for row in check_rows:
        if row["check_name"] in latest_by_name:
            continue
        latest_by_name.add(row["check_name"])
        checks.append({
            "name": row["check_name"],
            "passed": bool(row["passed"]),
            "exit_code": row["exit_code"],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
            "snapshot_matches_current": bool(snapshot and row["snapshot_hash"] == snapshot),
        })
    provider_events = []
    for event in store.events(task["id"], limit=1000):
        if event["type"].startswith(("PROVIDER_", "CLAUDE_")):
            payload = event["payload"]
            provider_events.append({
                "type": event["type"],
                "created_at": event["created_at"],
                "provider": payload.get("provider"),
                "event": payload.get("event"),
                "tools": payload.get("tools"),
                "item_type": payload.get("item_type"),
                "item_name": payload.get("item_name"),
            })
    current_head = run_git(store.root, ["rev-parse", "HEAD"], check=False)
    original_clean = not bool(git_status(store.root)) and current_head == task["base_sha"]
    return {
        "schema": "narrow.work-receipt.v1",
        "generated_at": now_iso(),
        "task": {
            "id": task["id"],
            "title": redact(task["title"]),
            "goal": redact(task["goal"]),
            "status": task["status"],
            "base_commit": task["base_sha"],
            "branch": task["branch"],
        },
        "workspace": {
            "snapshot_sha256": snapshot,
            "changed_paths": paths,
            "scope": scope,
            "unsafe_links": unsafe_links,
            "task_head_unchanged": run_git(workspace, ["rev-parse", "HEAD"], check=False) == task["base_sha"],
            "original_checkout_unchanged": original_clean,
        },
        "checks": checks,
        "acceptance": [
            {"criterion": index, "text": redact(item["text"]), "accepted": bool(item["accepted"]),
             "evidence": redact(item.get("evidence") or ""),
             "snapshot_matches_current": bool(snapshot and item.get("snapshot_hash") == snapshot)}
            for index, item in enumerate(task["acceptance"], 1)
        ],
        "provider_activity": provider_events[-100:],
        "safety_note": "NARROW's isolated Git copy is not an OS sandbox. Provider hooks protect only documented subsets of direct file operations. Human review remains required.",
    }


def _render_work_receipt_markdown(receipt: dict[str, Any]) -> str:
    task = receipt["task"]
    workspace = receipt["workspace"]
    lines = [
        f"# NARROW Work Receipt — {task['title']}",
        "",
        f"- Generated: `{receipt['generated_at']}`",
        f"- Task: `{task['id']}`",
        f"- Status: **{task['status']}**",
        f"- Base commit: `{task['base_commit']}`",
        f"- Isolated branch: `{task['branch']}`",
        f"- Workspace snapshot: `{workspace['snapshot_sha256'] or 'UNAVAILABLE — unsafe link detected'}`",
        "",
        "## Objective",
        task["goal"],
        "",
        "## Scope and changes",
        f"- Changed paths: {len(workspace['changed_paths'])}",
        f"- Outside allowlist: {len(workspace['scope']['outside'])}",
        f"- Explicitly denied: {len(workspace['scope']['denied'])}",
        f"- Unsafe links/special entries: {len(workspace['unsafe_links'])}",
        f"- Original checkout unchanged: {'yes' if workspace['original_checkout_unchanged'] else 'NO — review required'}",
        f"- Task branch HEAD unchanged: {'yes' if workspace['task_head_unchanged'] else 'NO — review required'}",
        "",
        *(["### Changed paths", *[f"- `{path}`" for path in workspace["changed_paths"]], ""] if workspace["changed_paths"] else ["- No changed paths detected.", ""]),
        "## Verification",
    ]
    if receipt["checks"]:
        for check in receipt["checks"]:
            result = "PASS" if check["passed"] else "FAIL"
            current = "current snapshot" if check["snapshot_matches_current"] else "STALE / DIFFERENT SNAPSHOT"
            lines.append(f"- **{check['name']}**: {result} (exit {check['exit_code']}; {check['duration_ms']} ms; {current}; {check['created_at']})")
    else:
        lines.append("- No verification runs recorded.")
    lines.extend(["", "## Acceptance criteria"])
    for item in receipt["acceptance"]:
        lines.append(f"- [{'x' if item['accepted'] else ' '}] {item['criterion']}. {item['text']}")
        if item["evidence"]:
            lines.append(f"  - Evidence: {item['evidence']}")
    lines.extend(["", "## Provider activity (sanitized)"])
    if receipt["provider_activity"]:
        for item in receipt["provider_activity"]:
            detail = item.get("tools") or item.get("item_name") or item.get("item_type") or item.get("event") or "activity"
            lines.append(f"- `{item['created_at']}` — {item.get('provider') or 'provider'} / {item['type']}: {detail}")
    else:
        lines.append("- No provider events recorded.")
    lines.extend(["", "> " + receipt["safety_note"], "", "NARROW does not commit, merge, push, deploy, or certify semantic correctness. Review the actual diff, provider output, test commands, and acceptance evidence before integration.", ""])
    return "\n".join(lines)


def cmd_report(args: argparse.Namespace) -> int:
    store = _store()
    task = store.get_task(args.task)
    receipt = _work_receipt(store, task)
    content = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else _render_work_receipt_markdown(receipt)
    if args.output:
        output = Path(args.output).expanduser()
        try:
            parent = output.parent.resolve(strict=True)
        except OSError as exc:
            raise NarrowError(f"Report parent directory does not exist or cannot be resolved: {output.parent}") from exc
        target = parent / output.name
        if target.is_symlink() or target.exists():
            raise NarrowError(f"Refusing to overwrite an existing or linked report path: {target}")
        try:
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except OSError as exc:
            raise NarrowError(f"Could not write the work receipt to {target}: {exc}") from exc
        print(f"Work receipt written to {target}")
    else:
        print(content, end="")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Start a loopback-only, read-only local task dashboard."""
    return run_dashboard(_store(), port=args.port, open_browser=args.open)


def cmd_finish(args: argparse.Namespace, _lock_fd: int | None = None, _store_obj: Store | None = None, _task: dict[str, Any] | None = None) -> int:
    if _lock_fd is None:
        store_obj = _store_obj or _store()
        task_obj = _task or store_obj.get_task(args.task)
        with task_execution_lock(store_obj.state_dir, task_obj["id"]) as acquired_fd:
            return cmd_finish(args, _lock_fd=acquired_fd, _store_obj=store_obj, _task=task_obj)
    store = _store_obj or _store()
    task = _task or store.get_task(args.task)
    success, problems = finish_task(store, task["id"])
    if success:
        print("VERIFIED_COMPLETE")
        print("All configured checks passed for the current workspace snapshot, every acceptance criterion was human-confirmed, and the original checkout remained unchanged.")
        print("Review the isolated diff before manually integrating it. NARROW has not merged, committed, or pushed anything.")
        return 0
    print("NEEDS_REVIEW — NARROW will not claim completion.")
    for item in problems:
        print(f"- {item}")
    return 2




def cmd_recover_agent(args: argparse.Namespace) -> int:
    """Recover one explicitly selected stale container-agent execution."""
    store = _store()
    task = store.get_task(args.task)
    if not args.yes:
        try:
            answer = input(
                f"Remove stale NARROW containers, credentials, and network for task {task['id']} / execution {args.execution}? [y/N] "
            ).strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("Cancelled. No runtime resource was changed.")
            return 1
    report = recover_agent_execution(store, task["id"], args.execution)
    print(json.dumps({
        "schema": "narrow.runtime-recovery.v1",
        "task_id": task["id"],
        **report,
    }, ensure_ascii=False, indent=2))
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="narrow",
        description="Safety-first task continuity and completion supervision for AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"NARROW {__version__}")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("init", help="Initialize local NARROW metadata and configuration.").set_defaults(func=cmd_init)

    setup = sub.add_parser("setup", help="Detect a safe project profile and prepare reviewed verification suggestions.")
    setup.add_argument("--profile", choices=("auto", "web", "python", "data", "general"), default="auto", help="Project preset (default: auto-detect).")
    setup.add_argument("--apply", action="store_true", help="Ask before saving the profile and adding missing check suggestions.")
    setup.add_argument("--json", action="store_true", help="Print the read-only setup plan as JSON; cannot be combined with --apply.")
    setup.set_defaults(func=cmd_setup)

    discover = sub.add_parser("discover", help="Read project manifests and suggest verification commands.")
    discover.add_argument("--apply", action="store_true", help="Review and add discovered checks to .narrow/config.json.")
    discover.add_argument("--json", action="store_true", help="Emit the discovery report as JSON.")
    discover.set_defaults(func=cmd_discover)

    propose = sub.add_parser("propose", help="Draft a task contract from a plain-language goal.")
    propose.add_argument("goal")
    propose.add_argument("--ollama", action="store_true", help="Use an explicitly selected local Ollama model; bounded source context is sent only to localhost.")
    propose.add_argument("--model", default="llama3:8b", help="Local Ollama model name (default: llama3:8b).")
    propose.add_argument("--json", action="store_true", help="Emit the task proposal as JSON.")
    propose.set_defaults(func=cmd_propose)

    contract_history = sub.add_parser("contract-history", help="Show generated and approved contract versions for a task.")
    contract_history.add_argument("--task")
    contract_history.add_argument("--json", action="store_true", help="Emit version history as JSON.")
    contract_history.set_defaults(func=cmd_contract_history)

    start = sub.add_parser("start", help="Create a task in an isolated repository copy.")
    start.add_argument("--title", help="Task title. Optional with --auto-contract.")
    start.add_argument("--goal", required=True)
    start.add_argument("--auto-contract", action="store_true", help="Draft paths and acceptance criteria from the goal, then ask before creating the task.")
    start.add_argument("--ollama", action="store_true", help="Use a model already running on local Ollama for the contract proposal.")
    start.add_argument("--model", default="llama3:8b", help="Local Ollama model name (default: llama3:8b).")
    start.add_argument("--allow", action="append", default=[], help="Allowed path glob; repeat for multiple patterns.")
    start.add_argument("--deny", action="append", default=[], help="Denied path glob; repeat for multiple patterns.")
    start.add_argument("--accept", action="append", default=[], help="Acceptance criterion; repeat for multiple criteria.")
    start.set_defaults(func=cmd_start)

    run = sub.add_parser("run", help="Run an installed AI agent inside the isolated task workspace.")
    run.add_argument("--task")
    run.add_argument("command", nargs=argparse.REMAINDER, help="Agent command after --, e.g. -- claude")
    run.set_defaults(func=cmd_run)

    resume = sub.add_parser("resume", help="Rebuild compact task memory and optionally launch another agent session.")
    resume.add_argument("--task")
    resume.add_argument("command", nargs=argparse.REMAINDER, help="Optional agent command after --, e.g. -- codex")
    resume.set_defaults(func=cmd_resume)

    drive = sub.add_parser(
        "drive",
        help="Run a bounded agent → checks → repair loop using a configured agent adapter.",
    )
    adapter_group = drive.add_mutually_exclusive_group(required=True)
    adapter_group.add_argument("--agent", help="Name of an agent entry in .narrow/config.json.")
    adapter_group.add_argument("--provider", help="Use a built-in or installed narrow.adapters provider plugin.")
    drive.add_argument("--model", help="Optional provider model name; provider defaults are used when omitted.")
    drive.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), help="Optional reasoning effort. `max` is Claude-only; supported levels depend on the selected model and account.")
    drive.add_argument("--max-turns", type=int, default=40, help="Claude Code turn budget (1–200; Codex ignores this setting).")
    drive.add_argument("--task")
    drive.add_argument("--max-attempts", type=int, help="Bounded repair budget (1–5); defaults to max_repair_attempts.")
    drive.add_argument("--max-budget-usd", type=float, help="Optional Claude client-side spend cap per attempt (0-1000 USD; estimate, not a billing guarantee).")
    drive.add_argument("--yes-checks", action="store_true", help="Approve configured checks without prompting; use only after reviewing them.")
    drive.add_argument("--allow-subagents", action="store_true", help="Opt in to Claude Agent/task tools. Subagents share the task workspace; NARROW monitors scope but this is not a separate sandbox.")
    drive.add_argument("--ultracode", action="store_true", help="Opt in to Claude Code dynamic multi-agent workflows (requires --allow-subagents; higher autonomy and cost).")
    drive.add_argument("--container-agent", action="store_true", help="Opt in to the experimental POSIX Docker Codex runtime. Requires digest-pinned agent_container images, OPENAI_API_KEY, and exact egress allowlist in .narrow/config.json.")
    drive.set_defaults(func=cmd_drive)

    providers = sub.add_parser("providers", help="Detect supported Claude Code and Codex CLI installations.")
    providers.add_argument("--versions", action="store_true", help="Run each detected CLI with --version (no sign-in check).")
    providers.add_argument("--json", action="store_true", help="Emit provider status as JSON.")
    providers.set_defaults(func=cmd_providers)

    security = sub.add_parser("security", help="Explain the separation between NARROW orchestration and OS-level enforcement.")
    security.add_argument("--json", action="store_true", help="Emit a machine-readable security posture report.")
    security.set_defaults(func=cmd_security)

    doctor = sub.add_parser("doctor", help="Run read-only diagnostics for this repository and local providers.")
    doctor.add_argument("--versions", action="store_true", help="Probe installed provider CLI versions.")
    doctor.set_defaults(func=cmd_doctor)

    hook = sub.add_parser("hook", help=argparse.SUPPRESS)
    hook.add_argument("hook_args", nargs=argparse.REMAINDER)
    hook.set_defaults(func=cmd_hook)

    tasks = sub.add_parser("tasks", help="List local tasks without exposing raw agent conversations.")
    tasks.add_argument("--all", action="store_true", help="Include verified/closed tasks.")
    tasks.add_argument("--limit", type=int, default=100, help="Maximum number of tasks to list (1–500).")
    tasks.add_argument("--json", action="store_true", help="Emit a stable, sanitized JSON task list.")
    tasks.set_defaults(func=cmd_tasks)

    status = sub.add_parser("status", help="Show current task, constraints, and acceptance state.")
    status.add_argument("--task")
    status.set_defaults(func=cmd_status)

    inspect = sub.add_parser("inspect", help="Classify changed paths against the task's scope.")
    inspect.add_argument("--task")
    inspect.set_defaults(func=cmd_inspect)

    note = sub.add_parser("note", help="Append a note to the local task memory.")
    note.add_argument("text")
    note.add_argument("--task")
    note.set_defaults(func=cmd_note)

    decision = sub.add_parser("decision", help="Append a decision to the local task memory.")
    decision.add_argument("text")
    decision.add_argument("--task")
    decision.set_defaults(func=cmd_decision)

    handoff = sub.add_parser("handoff", help="Generate a compact, resumable task handoff.")
    handoff.add_argument("--task")
    handoff.add_argument("--output", help="Optional file path; omit to print the handoff.")
    handoff.set_defaults(func=cmd_handoff)

    accept = sub.add_parser("accept", help="Human-confirm one acceptance criterion with evidence.")
    accept.add_argument("--criterion", type=int, required=True)
    accept.add_argument("--evidence", required=True)
    accept.add_argument("--task")
    accept.set_defaults(func=cmd_accept)

    verify = sub.add_parser("verify", help="Run one explicitly configured verification command.")
    verify.add_argument("--check", required=True)
    verify.add_argument("--task")
    verify.add_argument("--yes", action="store_true", help="Skip interactive confirmation; use only for trusted checks.")
    verify.set_defaults(func=cmd_verify)

    report = sub.add_parser("report", help="Generate a sanitized Work Receipt for the current task.")
    report.add_argument("--task")
    report.add_argument("--format", choices=("markdown", "json"), default="markdown")
    report.add_argument("--output", help="Optional new file path; existing files are never overwritten.")
    report.set_defaults(func=cmd_report)

    dashboard = sub.add_parser("dashboard", help="Open a read-only local Control Center on 127.0.0.1.")
    dashboard.add_argument("--port", type=int, default=8765, help="Loopback port (1024–65535; default 8765).")
    dashboard.add_argument("--open", action="store_true", help="Open the dashboard in the default browser.")
    dashboard.set_defaults(func=cmd_dashboard)

    recover = sub.add_parser(
        "recover-agent",
        help="Clean up one explicitly selected stale Docker agent execution after host interruption.",
    )
    recover.add_argument("--task", required=True, help="Exact NARROW task ID to recover.")
    recover.add_argument("--execution", required=True, help="Exact execution UUID to recover.")
    recover.add_argument("--yes", action="store_true", help="Confirm cleanup without an interactive prompt.")
    recover.set_defaults(func=cmd_recover_agent)

    finish = sub.add_parser("finish", help="Mark verified completion only when all gates pass.")
    finish.add_argument("--task")
    finish.set_defaults(func=cmd_finish)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except NarrowError as exc:
        print(f"NARROW: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. The isolated workspace and task journal were preserved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
