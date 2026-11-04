from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.supervisor import run_agent, run_verification_command
from narrow.drive import drive_task
from narrow.core import (
    NarrowError,
    Store,
    classify_paths,
    create_task,
    finish_task,
    save_check_run,
    init_project,
    make_handoff,
    redact,
    safe_git_environment,
    snapshot_hash,
    verify_check_run_signature,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def make_repo(parent: Path) -> Path:
    root = parent / "sample-project"
    root.mkdir(parents=True)
    git_init = subprocess.run(["git", "init", "-b", "main", str(root)], text=True, capture_output=True)
    if git_init.returncode != 0:
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        git(root, "checkout", "-b", "main")
    git(root, "config", "user.name", "NARROW Tests")
    git(root, "config", "user.email", "narrow-tests@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    (root / ".gitignore").write_text(".env\n__pycache__/\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial commit")
    return root


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.root = make_repo(self.temp_path)
        self.cache = self.temp_path / "cache"
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        self.env.start()
        init_project(self.root)
        self.store = Store(self.root)

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def new_task(self):
        return create_task(
            self.store,
            "Improve app behavior",
            "Make the app behavior more reliable without touching auth.",
            ["src/**", "tests/**"],
            ["src/auth/**"],
            ["The app behavior is covered by a passing test."],
        )

    def test_task_uses_isolated_clone_without_remotes_and_keeps_original_clean(self):
        git(self.root, "remote", "add", "origin", "https://example.invalid/demo/repo.git")
        original_head = git(self.root, "rev-parse", "HEAD")
        task = self.new_task()
        workspace = Path(task["worktree"])
        self.assertTrue(workspace.is_dir())
        self.assertNotEqual(workspace, self.root)
        self.assertTrue((workspace / ".git").is_dir())
        self.assertEqual(git(workspace, "branch", "--show-current"), task["branch"])
        self.assertEqual(git(workspace, "rev-parse", "HEAD"), original_head)
        self.assertEqual(git(workspace, "remote"), "")
        self.assertEqual(git(self.root, "remote", "get-url", "origin"), "https://example.invalid/demo/repo.git")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), original_head)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertTrue((workspace / "NARROW_TASK.md").exists())


    def test_init_keeps_local_narrow_state_out_of_git_status(self):
        probe = self.root / ".narrow" / "ignore-regression-probe"
        probe.write_text("local metadata\n", encoding="utf-8")
        self.assertEqual(git(self.root, "status", "--porcelain=v1", "--untracked-files=all"), "")

    def test_init_refuses_to_overwrite_tracked_reserved_path(self):
        other = make_repo(self.temp_path / "other")
        (other / "NARROW_TASK.md").write_text("User-owned content\n", encoding="utf-8")
        git(other, "add", "NARROW_TASK.md")
        git(other, "commit", "-m", "add reserved-looking file")
        with self.assertRaisesRegex(NarrowError, "already tracks a reserved NARROW path"):
            init_project(other)
        self.assertEqual((other / "NARROW_TASK.md").read_text(encoding="utf-8"), "User-owned content\n")

    def test_store_refuses_hardlinked_state_database(self):
        other = make_repo(self.temp_path / "hardlink-db-test")
        state = other / ".narrow"
        state.mkdir()
        outside = self.temp_path / "external-db-content.txt"
        outside.write_text("do not modify", encoding="utf-8")
        try:
            os.link(outside, state / "state.sqlite3")
        except OSError as exc:
            self.skipTest(f"Hard links are unavailable in this environment: {exc}")
        with self.assertRaisesRegex(NarrowError, "private regular file"):
            Store(other)
        self.assertEqual(outside.read_text(encoding="utf-8"), "do not modify")

    def test_init_refuses_symbolic_link_git_exclude_file(self):
        other = make_repo(self.temp_path / "symlink-exclude-test")
        exclude = other / ".git" / "info" / "exclude"
        outside = self.temp_path / "external-exclude-target.txt"
        outside.write_text("keep this target", encoding="utf-8")
        try:
            exclude.unlink(missing_ok=True)
            exclude.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are unavailable in this environment: {exc}")
        with self.assertRaisesRegex(NarrowError, "symbolic link"):
            Store(other)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep this target")

    def test_init_refuses_hardlinked_git_exclude_file(self):
        other = make_repo(self.temp_path / "hardlink-exclude-test")
        exclude = other / ".git" / "info" / "exclude"
        outside = self.temp_path / "external-exclude.txt"
        outside.write_text("keep me", encoding="utf-8")
        try:
            exclude.unlink(missing_ok=True)
            os.link(outside, exclude)
        except OSError as exc:
            self.skipTest(f"Hard links are unavailable in this environment: {exc}")
        with self.assertRaisesRegex(NarrowError, "linked or not a regular file"):
            Store(other)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")

    def test_init_refuses_symbolic_link_for_narrow_state_directory(self):
        other = make_repo(self.temp_path / "symlink-test")
        external = self.temp_path / "external-state"
        external.mkdir()
        try:
            (other / ".narrow").symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are unavailable in this environment: {exc}")
        with self.assertRaisesRegex(NarrowError, "symbolic link"):
            init_project(other)
        self.assertEqual(list(external.iterdir()), [])

    def test_internal_git_commands_ignore_inherited_repository_redirection(self):
        other = make_repo(self.temp_path / "redirect-target")
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            from narrow.core import run_git
            self.assertEqual(Path(run_git(self.root, ["rev-parse", "--show-toplevel"])).resolve(), self.root.resolve())

    def test_internal_git_commands_do_not_run_global_fsmonitor_hook(self):
        other = make_repo(self.temp_path / "fsmonitor-test")
        hook = self.temp_path / "fsmonitor-hook.sh"
        marker = self.temp_path / "fsmonitor-was-run.txt"
        hook.write_text(f"#!/bin/sh\nprintf ran > '{marker}'\nprintf '\\n\\n'\n", encoding="utf-8")
        hook.chmod(0o755)
        global_config = self.temp_path / "gitconfig-fsmonitor"
        subprocess.run(["git", "config", "--file", str(global_config), "core.fsmonitor", str(hook)], check=True)
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}):
            from narrow.core import run_git
            run_git(other, ["status", "--porcelain=v1"])
        self.assertFalse(marker.exists())

    def test_initial_clone_checkout_does_not_run_global_post_checkout_hook(self):
        other = make_repo(self.temp_path / "hook-test")
        hook_dir = self.temp_path / "global-hooks"
        hook_dir.mkdir()
        marker = self.temp_path / "hook-was-run.txt"
        hook = hook_dir / "post-checkout"
        hook.write_text(f"#!/bin/sh\nprintf ran > '{marker}'\n", encoding="utf-8")
        hook.chmod(0o755)
        global_config = self.temp_path / "gitconfig"
        subprocess.run(["git", "config", "--file", str(global_config), "core.hooksPath", str(hook_dir)], check=True)
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}):
            store = Store(other)
            task = create_task(store, "Task", "Goal", ["src/**"], [], ["Done"])
        self.assertFalse(marker.exists())
        self.assertTrue(Path(task["worktree"]).is_dir())

    def test_init_refuses_nonempty_user_narrow_directory(self):
        other = make_repo(self.temp_path / "other2")
        user_state = other / ".narrow"
        user_state.mkdir()
        (user_state / "notes.txt").write_text("Keep this file", encoding="utf-8")
        with self.assertRaisesRegex(NarrowError, "non-empty .narrow"):
            init_project(other)
        self.assertEqual((user_state / "notes.txt").read_text(encoding="utf-8"), "Keep this file")

    def test_start_refuses_dirty_original_worktree(self):
        (self.root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(NarrowError, "working tree is not clean"):
            self.new_task()

    def test_store_list_tasks_is_bounded_and_excludes_closed_by_default(self):
        first = self.new_task()
        second = create_task(
            self.store,
            "Second task",
            "A separate task.",
            ["src/**"],
            [],
            ["Second criterion is met."],
        )
        active = self.store.list_tasks()
        self.assertEqual([item["id"] for item in active], [second["id"], first["id"]])
        self.store.update_status(first["id"], "CLOSED")
        self.assertEqual([item["id"] for item in self.store.list_tasks()], [second["id"]])
        self.assertEqual({item["id"] for item in self.store.list_tasks(include_closed=True)}, {first["id"], second["id"]})
        self.assertEqual(len(self.store.list_tasks(include_closed=True, limit=1)), 1)
        with self.assertRaises(Exception):
            self.store.list_tasks(limit=0)

    def test_start_requires_allowlist_and_acceptance(self):
        with self.assertRaisesRegex(NarrowError, "--allow"):
            create_task(self.store, "Task", "Goal", [], [], ["Done"])
        with self.assertRaisesRegex(NarrowError, "--accept"):
            create_task(self.store, "Task", "Goal", ["src/**"], [], [])

    def test_classify_paths_fails_closed_for_unlisted_paths(self):
        result = classify_paths(
            ["src/app.py", "src/auth/session.py", "README.md"],
            ["src/**"],
            ["src/auth/**"],
        )
        self.assertEqual(result["allowed"], ["src/app.py"])
        self.assertEqual(result["denied"], ["src/auth/session.py"])
        self.assertEqual(result["outside"], ["README.md"])

    def test_path_globs_do_not_allow_nested_paths_with_single_star(self):
        result = classify_paths(
            ["src/app.py", "src/auth/session.py", ".env"],
            ["src/*"],
            [".env"],
        )
        self.assertEqual(result["allowed"], ["src/app.py"])
        self.assertEqual(result["denied"], [".env"])
        self.assertEqual(result["outside"], ["src/auth/session.py"])

    def test_snapshot_hash_changes_when_file_changes(self):
        task = self.new_task()
        worktree = Path(task["worktree"])
        target = worktree / "src" / "app.py"
        target.write_text("VALUE = 2\n", encoding="utf-8")
        first = snapshot_hash(worktree)
        target.write_text("VALUE = 3\n", encoding="utf-8")
        second = snapshot_hash(worktree)
        self.assertNotEqual(first, second)

    def test_handoff_contains_goal_scope_and_acceptance(self):
        task = self.new_task()
        handoff = make_handoff(self.store, task["id"])
        self.assertIn("Original goal", handoff)
        self.assertIn("Make the app behavior more reliable", handoff)
        self.assertIn("src/**", handoff)
        self.assertIn("The app behavior is covered", handoff)

    def test_notes_are_redacted_before_persistence(self):
        task = self.new_task()
        self.store.add_event(task["id"], "NOTE", {"text": "api_key=super-secret-value"})
        events = self.store.events(task["id"])
        self.assertIn("[REDACTED]", events[-1]["payload"]["text"])
        self.assertNotIn("super-secret-value", events[-1]["payload"]["text"])

    def test_common_secret_patterns_are_redacted(self):
        text = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 and api_key=abcdef"
        result = redact(text)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", result)
        self.assertNotIn("abcdef", result)

    def test_terminal_escape_sequences_are_removed_from_persisted_text(self):
        result = redact("safe\x1b[31mRED\x1b[0m\x07text")
        self.assertEqual(result, "safeRED�text")


    def test_ignored_files_are_included_in_scope_monitor(self):
        task = self.new_task()
        worktree = Path(task["worktree"])
        (worktree / ".env").write_text("PRIVATE_VALUE=not-for-git\n", encoding="utf-8")
        from narrow.core import changed_paths
        self.assertIn(".env", changed_paths(worktree))

    def test_finish_requires_current_snapshot_checks_and_human_acceptance(self):
        task = self.new_task()
        worktree = Path(task["worktree"])
        (worktree / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        config_path = self.root / ".narrow" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["checks"] = {
            "unit": {"command": [sys.executable, "-c", "pass"], "timeout_seconds": 30}
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        current_snapshot = snapshot_hash(worktree)
        save_check_run(self.store, task["id"], "unit", [sys.executable, "-c", "pass"], current_snapshot, 0, 10, "ok")
        from narrow.core import accept_criterion
        accept_criterion(self.store, task["id"], 1, "Reviewed the behavior and its test coverage.")
        success, problems = finish_task(self.store, task["id"])
        self.assertTrue(success, problems)
        self.assertEqual(self.store.get_task(task["id"])["status"], "VERIFIED_COMPLETE")
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_supervisor_refuses_preexisting_external_symlink(self):
        task = self.new_task()
        workspace = Path(task["worktree"])
        external = self.temp_path / "outside-workspace.txt"
        external.write_text("safe", encoding="utf-8")
        link = workspace / "src" / "outside.txt"
        try:
            link.symlink_to(external)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are unavailable in this environment: {exc}")
        with self.assertRaisesRegex(NarrowError, "unsafe symlinks or hard-linked"):
            run_agent(self.store, task["id"], [sys.executable, "-c", "pass"])
        self.assertEqual(external.read_text(encoding="utf-8"), "safe")

    def test_agent_git_commands_are_not_redirected_by_inherited_git_dir(self):
        task = self.new_task()
        other = make_repo(self.temp_path / "agent-git-redirection")
        code = (
            "import subprocess; "
            "p=subprocess.run(['git','rev-parse','--show-toplevel'],check=True,text=True,capture_output=True); "
            "print(p.stdout.strip())"
        )
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            exit_code = run_agent(self.store, task["id"], [sys.executable, "-c", code])
        self.assertEqual(exit_code, 0)
        self.assertEqual(git(Path(task["worktree"]), "rev-parse", "--show-toplevel"), str(Path(task["worktree"])))

    def test_supervisor_stops_when_agent_changes_out_of_scope_file(self):
        task = self.new_task()
        config_path = self.root / ".narrow" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["monitor_interval_seconds"] = 0.5
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; import time; Path('README.md').write_text('outside scope'); time.sleep(10)",
        ]
        exit_code = run_agent(self.store, task["id"], command)
        self.assertEqual(exit_code, 125)
        self.assertTrue((Path(task["worktree"]) / "README.md").exists())
        self.assertFalse((self.root / "README.md").exists())
        events = self.store.events(task["id"])
        self.assertTrue(any(event["type"] == "SCOPE_VIOLATION" for event in events))

    def test_supervisor_final_scan_catches_fast_out_of_scope_write(self):
        task = self.new_task()
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('README.md').write_text('outside scope');",
        ]
        exit_code = run_agent(self.store, task["id"], command)
        self.assertEqual(exit_code, 125)
        events = self.store.events(task["id"], limit=100)
        self.assertTrue(any(event["type"] in {"POST_RUN_SAFETY_REVIEW", "SCOPE_VIOLATION"} for event in events))

    def test_supervisor_refuses_to_disable_scope_violation_stop(self):
        task = self.new_task()
        config_path = self.root / ".narrow" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["stop_on_scope_violation"] = False
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(NarrowError, "must remain true"):
            run_agent(self.store, task["id"], [sys.executable, "-c", "pass"])

    def test_supervisor_detects_task_contract_tampering(self):
        task = self.new_task()
        workspace = Path(task["worktree"])
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('NARROW_TASK.md').write_text('changed contract');",
        ]
        exit_code = run_agent(self.store, task["id"], command)
        self.assertEqual(exit_code, 125)
        events = self.store.events(task["id"], limit=100)
        self.assertTrue(any(event["type"] == "HELPER_FILE_CHANGED" for event in events))
        self.assertTrue((workspace / "NARROW_TASK.md").exists())

    def test_supervisor_restores_modified_task_contract_before_resume(self):
        task = self.new_task()
        workspace = Path(task["worktree"])
        brief = workspace / "NARROW_TASK.md"
        brief.write_text("Agent changed the task contract", encoding="utf-8")
        exit_code = run_agent(self.store, task["id"], [sys.executable, "-c", "pass"], resume=True)
        self.assertEqual(exit_code, 0)
        self.assertIn("Make the app behavior more reliable", brief.read_text(encoding="utf-8"))
        events = self.store.events(task["id"], limit=100)
        self.assertTrue(any(event["type"] == "TASK_BRIEF_RESTORED" for event in events))

    def test_resume_replaces_handoff_symlink_without_writing_to_target(self):
        task = self.new_task()
        workspace = Path(task["worktree"])
        outside = self.temp_path / "do-not-overwrite.txt"
        outside.write_text("keep this content", encoding="utf-8")
        handoff = workspace / "NARROW_HANDOFF.md"
        try:
            handoff.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are unavailable in this environment: {exc}")
        exit_code = run_agent(self.store, task["id"], [sys.executable, "-c", "pass"], resume=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep this content")
        self.assertFalse(handoff.is_symlink())
        self.assertIn("NARROW handoff", handoff.read_text(encoding="utf-8"))

    def test_finish_refuses_a_modified_task_contract_even_after_checks(self):
        task = self.new_task()
        brief = Path(task["worktree"]) / "NARROW_TASK.md"
        brief.write_text("A different instruction", encoding="utf-8")
        success, problems = finish_task(self.store, task["id"])
        self.assertFalse(success)
        self.assertTrue(any("authoritative task contract" in item for item in problems))
        self.assertEqual(self.store.get_task(task["id"])["status"], "NEEDS_REVIEW")

    def test_finish_refuses_symlinks_that_escape_the_task_workspace(self):
        task = self.new_task()
        workspace = Path(task["worktree"])
        external = self.temp_path / "external-target.txt"
        external.write_text("do not touch", encoding="utf-8")
        link = workspace / "src" / "external-link.txt"
        try:
            link.symlink_to(external)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are unavailable in this environment: {exc}")
        success, problems = finish_task(self.store, task["id"])
        self.assertFalse(success)
        self.assertTrue(any("Unsafe symlinks or hard-linked files" in item for item in problems))
        self.assertEqual(external.read_text(encoding="utf-8"), "do not touch")

    def test_finish_refuses_hardlinked_workspace_files(self):
        task = self.new_task()
        workspace = Path(task["worktree"])
        external = self.temp_path / "outside-hardlink-target.txt"
        external.write_text("keep this data", encoding="utf-8")
        linked = workspace / "src" / "linked-copy.txt"
        try:
            os.link(external, linked)
        except OSError as exc:
            self.skipTest(f"Hard links are unavailable in this environment: {exc}")
        success, problems = finish_task(self.store, task["id"])
        self.assertFalse(success)
        self.assertTrue(any("Unsafe symlinks or hard-linked files" in item for item in problems))
        self.assertEqual(external.read_text(encoding="utf-8"), "keep this data")

    def test_finish_never_claims_complete_without_configured_checks(self):
        task = self.new_task()
        success, problems = finish_task(self.store, task["id"])
        self.assertFalse(success)
        self.assertTrue(any("No verification checks are configured" in item for item in problems))
        self.assertEqual(self.store.get_task(task["id"])["status"], "NEEDS_REVIEW")

    def test_finish_requires_the_same_verification_command_that_was_approved_and_run(self):
        task = self.new_task()
        workspace = Path(task["worktree"])
        (workspace / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        config_path = self.root / ".narrow" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        original_command = [sys.executable, "-c", "pass"]
        config["checks"] = {"unit": {"command": original_command, "timeout_seconds": 30}}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        current_snapshot = snapshot_hash(workspace)
        save_check_run(self.store, task["id"], "unit", original_command, current_snapshot, 0, 10, "ok")
        config["checks"]["unit"]["command"] = [sys.executable, "-c", "raise SystemExit(1)"]
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        success, problems = finish_task(self.store, task["id"])
        self.assertFalse(success)
        self.assertTrue(any("command changed" in item for item in problems))

    def test_acceptance_evidence_is_invalidated_when_workspace_changes(self):
        task = self.new_task()
        workspace = Path(task["worktree"])
        (workspace / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        config_path = self.root / ".narrow" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["checks"] = {"unit": {"command": [sys.executable, "-c", "pass"], "timeout_seconds": 30}}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        current_snapshot = snapshot_hash(workspace)
        save_check_run(self.store, task["id"], "unit", [sys.executable, "-c", "pass"], current_snapshot, 0, 10, "ok")
        from narrow.core import accept_criterion
        accept_criterion(self.store, task["id"], 1, "Reviewed the current implementation.")
        (workspace / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
        success, problems = finish_task(self.store, task["id"])
        self.assertFalse(success)
        self.assertTrue(any("Acceptance evidence is stale" in item for item in problems))



class DriveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.root = make_repo(self.temp_path)
        self.cache = self.temp_path / "cache"
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        self.env.start()
        init_project(self.root)
        self.store = Store(self.root)
        self.task = create_task(
            self.store,
            "Repair the sample value",
            "Change src/app.py so VALUE equals 2.",
            ["src/**"],
            [],
            ["src/app.py defines VALUE = 2."],
        )
        self.fake_agent = self.temp_path / "fake_agent.py"
        self.fake_agent.write_text(
            "from pathlib import Path\n"
            "import re, sys\n"
            "prompt = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
            "attempt = int(re.search(r'Attempt: (\\d+)', prompt).group(1))\n"
            "if attempt > 1 and not Path(__import__('os').environ['NARROW_HANDOFF_FILE']).exists():\n"
            "    raise SystemExit('handoff missing on repair attempt')\n"
            "Path('src/app.py').write_text('VALUE = 1\\n' if attempt == 1 else 'VALUE = 2\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.check = self.temp_path / "check.py"
        self.check.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "sys.exit(0 if Path('src/app.py').read_text(encoding='utf-8').strip() == 'VALUE = 2' else 1)\n",
            encoding="utf-8",
        )
        config_path = self.root / ".narrow" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agents"] = {
            "fake": {"command": [sys.executable, str(self.fake_agent), "{prompt_file}"]}
        }
        config["checks"] = {
            "sample": {"command": [sys.executable, str(self.check)], "timeout_seconds": 30}
        }
        config["max_repair_attempts"] = 2
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_verification_command_retains_bounded_output_without_pipe_deadlock(self):
        env = os.environ.copy()
        code, output = run_verification_command(
            [sys.executable, "-c", "print('x' * 200000)"],
            cwd=Path(self.task["worktree"]),
            env=env,
            timeout=10,
        )
        self.assertEqual(code, 0)
        self.assertLessEqual(len(output.encode("utf-8")), 64 * 1024)
        self.assertTrue(output.rstrip().endswith("x"))

    def test_verification_timeout_stops_the_command_and_returns_a_clear_failure(self):
        env = os.environ.copy()
        code, output = run_verification_command(
            [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(10)"],
            cwd=Path(self.task["worktree"]),
            env=env,
            timeout=1,
        )
        self.assertEqual(code, 124)
        self.assertIn("started", output)
        self.assertIn("Timed out after 1 seconds", output)

    def test_drive_stops_if_verification_mutates_the_workspace(self):
        mutating_check = self.temp_path / "mutating_check.py"
        mutating_check.write_text(
            "from pathlib import Path\n"
            "Path('src/app.py').write_text('VALUE = 99\\n', encoding='utf-8')\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        config_path = self.root / ".narrow" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["checks"]["sample"]["command"] = [sys.executable, str(mutating_check)]
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        result = drive_task(self.store, self.task["id"], "fake", max_attempts=2, yes_checks=True)
        self.assertEqual(result, 2)
        self.assertEqual(self.store.get_task(self.task["id"])["status"], "NEEDS_REVIEW")
        with self.store.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM check_runs WHERE task_id=?", (self.task["id"],)).fetchone()[0]
        self.assertEqual(count, 1)
        events = self.store.events(self.task["id"], limit=100)
        self.assertTrue(any(event["type"] == "DRIVE_STOPPED" for event in events))

    def test_drive_repairs_failed_check_with_bounded_second_attempt(self):
        result = drive_task(self.store, self.task["id"], "fake", max_attempts=2, yes_checks=True)
        self.assertEqual(result, 0)
        workspace = Path(self.task["worktree"])
        self.assertEqual((workspace / "src" / "app.py").read_text(encoding="utf-8").strip(), "VALUE = 2")
        self.assertTrue((workspace / "NARROW_HANDOFF.md").exists())
        self.assertIn("Attempt: 2", (workspace / "NARROW_PROMPT.md").read_text(encoding="utf-8"))
        self.assertEqual(self.store.get_task(self.task["id"])["status"], "READY_FOR_REVIEW")
        events = self.store.events(self.task["id"], limit=100)
        self.assertTrue(any(event["type"] == "REPAIR_REQUESTED" for event in events))
        with self.store.connect() as db:
            rows = db.execute("SELECT passed FROM check_runs WHERE task_id=? ORDER BY id", (self.task["id"],)).fetchall()
        self.assertEqual([row["passed"] for row in rows], [0, 1])
        # Passing checks do not replace the human acceptance gate.
        success, problems = finish_task(self.store, self.task["id"])
        self.assertFalse(success)
        self.assertTrue(any("human confirmation" in item for item in problems))


    def test_safe_git_environment_preserves_only_core_autocrlf_from_repo_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "core.autocrlf", "true"],
                check=True,
                capture_output=True,
            )
            env = safe_git_environment(root=root)
            self.assertEqual(env.get("GIT_CONFIG_NOSYSTEM"), "1")
            self.assertEqual(env.get("GIT_CONFIG_GLOBAL"), os.devnull)
            self.assertEqual(env.get("GIT_CONFIG_COUNT"), "1")
            self.assertEqual(env.get("GIT_CONFIG_KEY_0"), "core.autocrlf")
            self.assertEqual(env.get("GIT_CONFIG_VALUE_0"), "true")
            self.assertNotIn("GIT_DIR", env)
            self.assertNotIn("GIT_WORK_TREE", env)

    def test_check_evidence_signature_detects_database_row_tampering(self):
        task = self.task
        workspace = Path(task["worktree"])
        snapshot = snapshot_hash(workspace)
        save_check_run(
            self.store, task["id"], "unit", ["python", "-m", "pytest"],
            snapshot, 0, 42, "all tests passed",
        )
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM check_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task["id"],),
            ).fetchone()
        self.assertTrue(verify_check_run_signature(self.store, row))
        with self.store.connect() as db:
            db.execute("UPDATE check_runs SET output_tail=? WHERE id=?", ("forged success", row["id"]))
        with self.store.connect() as db:
            changed = db.execute("SELECT * FROM check_runs WHERE id=?", (row["id"],)).fetchone()
        self.assertFalse(verify_check_run_signature(self.store, changed))



if __name__ == "__main__":
    unittest.main()
