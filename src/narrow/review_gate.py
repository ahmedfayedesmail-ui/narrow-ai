"""Deterministic, read-only review evidence for the NARROW human gate.

This module is intentionally not an LLM judge. It validates locally available
machine evidence and reports risk indicators; it never approves, executes,
merges, commits, or changes task state.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .core import (
    NarrowError,
    Store,
    changed_paths,
    classify_paths,
    git_status,
    load_config,
    redact,
    render_task_brief,
    run_git,
    safe_git_environment,
    snapshot_hash,
    task_workspace,
    unsafe_workspace_links,
    verify_check_run_signature,
)


_SECRET_NAMES = {
    ".env", ".env.local", ".env.development", ".env.production",
    ".npmrc", ".pypirc", ".netrc", "credentials", "secrets",
    "id_rsa", "id_ed25519",
}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}
_SENSITIVE_TOKENS = {
    "auth", "authentication", "authorization", "security", "sandbox",
    "permissions", "policy", "workflow", "workflows", "deploy",
    "deployment", "billing", "payment", "credential", "credentials",
}


def _path_risks(paths: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for path in paths:
        normalized = path.replace("\\", "/")
        parts = [part.lower() for part in normalized.split("/") if part]
        name = parts[-1] if parts else ""
        is_example_env = name in {".env.example", ".env.sample", ".env.template"}
        secret_match = (
            not is_example_env
            and (
                name in _SECRET_NAMES
                or Path(name).suffix.lower() in _SECRET_SUFFIXES
                or any(part in {"secrets", "credentials"} for part in parts[:-1])
                or name.startswith("id_rsa")
                or name.startswith("id_ed25519")
            )
        )
        if secret_match:
            blockers.append({
                "code": "SENSITIVE_FILE_PATH",
                "severity": "critical",
                "path": normalized,
                "detail": "A credential-like file path changed. The reviewer does not open its contents; remove it from the change or handle it through an approved secret-management process.",
            })
            continue

        tokens = set(re.split(r"[^a-z0-9]+", normalized.lower()))
        if (
            ".github/workflows" in normalized.lower()
            or name.startswith("dockerfile")
            or name.startswith("compose")
            or tokens.intersection(_SENSITIVE_TOKENS)
        ):
            warnings.append({
                "code": "SECURITY_SENSITIVE_AREA",
                "severity": "high",
                "path": normalized,
                "detail": "This change touches an authentication, security, policy, deployment, workflow, billing, or runtime boundary and needs focused human review.",
            })
    return blockers, warnings


def build_review_report(store: Store, task_id: str) -> dict[str, Any]:
    """Build a deterministic review packet without mutating task state or running checks."""
    task = store.get_task(task_id)
    workspace = task_workspace(task)
    paths = changed_paths(workspace)
    unsafe = unsafe_workspace_links(workspace)
    scope = classify_paths(paths, task["allowed"], task["denied"])
    current_snapshot = snapshot_hash(workspace)

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []

    def gate(name: str, passed: bool, detail: str, *, blocker: bool = True) -> None:
        item = {"name": name, "status": "PASS" if passed else ("BLOCKED" if blocker else "NEEDS_ATTENTION"), "detail": detail}
        gates.append(item)
        if not passed and blocker:
            blockers.append({"code": name.upper(), "severity": "high", "detail": detail})

    # The local task brief is a protected projection of the authoritative DB contract.
    brief = workspace / "NARROW_TASK.md"
    try:
        metadata = brief.lstat()
        brief_ok = brief.is_file() and not brief.is_symlink() and metadata.st_nlink == 1
        if brief_ok:
            brief_ok = brief.read_text(encoding="utf-8") == render_task_brief(task)
    except (OSError, UnicodeError):
        brief_ok = False
    gate("task_contract", brief_ok, "Task brief matches the authoritative local task contract." if brief_ok else "NARROW_TASK.md is missing, linked, hard-linked, unreadable, or differs from the authoritative task contract.")

    gate("workspace_integrity", not unsafe, "No unsafe links or hard-linked files were detected." if not unsafe else "Unsafe workspace entries were detected: " + ", ".join(unsafe[:20]))
    gate("scope", not scope["outside"] and not scope["denied"], "All changed paths match the task allow/deny contract." if not scope["outside"] and not scope["denied"] else "Out-of-scope paths: " + ", ".join(scope["outside"][:20]) + ("; " if scope["outside"] and scope["denied"] else "") + "Denied paths: " + ", ".join(scope["denied"][:20]))

    config = load_config(store.root)
    maximum = config.get("max_changed_files", 100)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        raise NarrowError("max_changed_files in .narrow/config.json must be a positive integer.")
    gate("change_budget", len(paths) <= maximum, f"{len(paths)} changed paths; configured maximum is {maximum}.")

    # Check whitespace/conflict markers in tracked diffs using sanitized Git config.
    try:
        diff_check = subprocess.run(
            ["git", "-C", str(workspace), "diff", "--check", task["base_sha"], "--"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
            env=safe_git_environment(root=workspace),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise NarrowError(f"Could not run the read-only Git diff check: {type(exc).__name__}.") from exc
    diff_clean = diff_check.returncode == 0
    diff_detail = "Git diff --check found no whitespace errors in tracked changes." if diff_clean else "Git diff --check reported issues: " + (diff_check.stdout or diff_check.stderr).strip()[:1200]
    gate("diff_hygiene", diff_clean, diff_detail)

    # Verify the original checkout is still exactly at the task baseline and clean.
    original_head = run_git(store.root, ["rev-parse", "HEAD"])
    original_clean = not git_status(store.root)
    gate("original_checkout", original_head == task["base_sha"] and original_clean, "Original checkout remains clean at the task baseline." if original_head == task["base_sha"] and original_clean else "Original checkout HEAD or working tree changed during the task.")
    worktree_head = run_git(workspace, ["rev-parse", "HEAD"])
    worktree_branch = run_git(workspace, ["branch", "--show-current"], check=False)
    gate("task_branch", worktree_head == task["base_sha"] and worktree_branch == task["branch"], "Task branch and HEAD remain at the expected baseline; no agent commit or branch switch was detected." if worktree_head == task["base_sha"] and worktree_branch == task["branch"] else "The task branch or HEAD changed; inspect manually.")

    secret_blockers, path_warnings = _path_risks(paths)
    blockers.extend(secret_blockers)
    warnings.extend(path_warnings)
    for item in secret_blockers:
        gates.append({"name": "sensitive_file_path", "status": "BLOCKED", "detail": f"{item['path']}: {item['detail']}"})
    for item in path_warnings:
        gates.append({"name": "security_sensitive_area", "status": "HUMAN_SCRUTINY", "detail": f"{item['path']}: {item['detail']}"})

    # Latest check evidence must be signed, successful, configured identically,
    # and bound to this exact workspace snapshot.
    checks_config = config.get("checks", {})
    if not isinstance(checks_config, dict):
        raise NarrowError("The 'checks' entry in .narrow/config.json must be an object.")
    with store.connect() as db:
        rows = db.execute("SELECT * FROM check_runs WHERE task_id=? ORDER BY id DESC", (task_id,)).fetchall()
    latest: dict[str, Any] = {}
    for row in rows:
        latest.setdefault(row["check_name"], row)

    check_results: list[dict[str, Any]] = []
    for name, spec in checks_config.items():
        row = latest.get(name)
        status = "MISSING"
        detail = "No run has been recorded for this configured check."
        if row is not None:
            if not verify_check_run_signature(store, row):
                status, detail = "INVALID_EVIDENCE", "The evidence signature or recorded payload is invalid; rerun this check."
            else:
                try:
                    recorded_command = json.loads(row["command_json"])
                except (TypeError, json.JSONDecodeError):
                    recorded_command = None
                command = spec.get("command") if isinstance(spec, dict) else None
                configured_command = [redact(part) for part in command] if isinstance(command, list) and all(isinstance(part, str) for part in command) else None
                if recorded_command != configured_command:
                    status, detail = "CONFIG_CHANGED", "The configured command differs from the signed command; rerun this check."
                elif row["snapshot_hash"] != current_snapshot:
                    status, detail = "STALE", "The task workspace changed after this check; rerun on the current snapshot."
                elif not row["passed"]:
                    status, detail = "FAILED", "The latest signed verification result failed."
                else:
                    status, detail = "PASS_CURRENT", "Signed verification passed for the current workspace snapshot."
        check_results.append({"name": name, "status": status, "detail": detail, "duration_ms": row["duration_ms"] if row is not None else None})
    checks_ready = bool(checks_config) and all(item["status"] == "PASS_CURRENT" for item in check_results)
    gate("verification_evidence", checks_ready, "Every configured check has valid, signed, passing evidence for the current snapshot." if checks_ready else "Configured checks are missing, failed, stale, reconfigured, or have invalid signatures.")

    acceptance_rows = task["acceptance"]
    accepted_current: list[int] = []
    pending_acceptance: list[int] = []
    for index, item in enumerate(acceptance_rows, 1):
        valid = bool(item.get("accepted")) and bool(item.get("evidence"))
        current = valid and item.get("snapshot_hash") == current_snapshot
        if current:
            accepted_current.append(index)
        else:
            pending_acceptance.append(index)
    acceptance_ready = bool(acceptance_rows) and not pending_acceptance
    gate("human_acceptance", acceptance_ready, "Every acceptance criterion has current human evidence." if acceptance_ready else "Human confirmation is still needed or has become stale for criteria: " + ", ".join(map(str, pending_acceptance)), blocker=False)

    if blockers:
        decision = "BLOCKED"
        next_action = "Fix the blocking findings, then rerun narrow review."
    elif not checks_ready:
        decision = "NEEDS_VERIFICATION"
        next_action = "Run the configured verification checks and rerun narrow review."
    elif not acceptance_ready or task["status"] != "READY_FOR_REVIEW":
        decision = "HUMAN_REVIEW_REQUIRED"
        next_action = "Review the diff and evidence, confirm each acceptance criterion, then run narrow finish."
    else:
        decision = "READY_FOR_FINISH_GATE"
        next_action = "Run narrow finish. It independently rechecks all completion invariants."
    return {
        "schema": "narrow.review-report.v1",
        "task": {"id": task_id, "title": task["title"], "status": task["status"]},
        "decision": decision,
        "automatic_approval": False,
        "state_mutated": False,
        "commands_executed": False,
        "snapshot_hash": current_snapshot,
        "changed_files": {"total": len(paths), "allowed": scope["allowed"][:100], "outside": scope["outside"][:100], "denied": scope["denied"][:100], "unsafe": unsafe[:50]},
        "gates": gates,
        "verification": {"configured": len(checks_config), "passing_current": sum(1 for item in check_results if item["status"] == "PASS_CURRENT"), "items": check_results},
        "acceptance": {"total": len(acceptance_rows), "accepted_current": accepted_current, "pending_or_stale": pending_acceptance},
        "risk_flags": warnings,
        "blockers": blockers,
        "next_action": next_action,
    }
