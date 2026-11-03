from __future__ import annotations

import io
import json
import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.cli import _render_work_receipt_markdown, _work_receipt, build_parser
from narrow.core import Store, classify_paths, create_task, init_project
from narrow.hooks import _validate_direct_write, handle_claude_hook
from narrow.providers import (
    ClaudeCodeAdapter,
    CodexCliAdapter,
    claude_settings_path,
    latest_provider_session_id,
    make_event_callback,
    provider_status,
)
from narrow.supervisor import run_agent


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def make_repo(parent: Path) -> Path:
    root = parent / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    git(root, "config", "user.name", "NARROW tests")
    git(root, "config", "user.email", "narrow@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "README.md").write_text("sample\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


class BytesInput:
    def __init__(self, value: dict):
        self.buffer = io.BytesIO(json.dumps(value).encode("utf-8"))


class ProviderAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.temp_path / "cache")})
        self.env.start()
        self.root = make_repo(self.temp_path)
        init_project(self.root)
        self.store = Store(self.root)
        self.task = create_task(
            self.store,
            "Improve application",
            "Improve application behavior safely.",
            ["src/**"],
            ["src/secrets/**"],
            ["The current test suite passes."],
        )
        self.workspace = Path(self.task["worktree"])
        self.prompt = self.workspace / "NARROW_PROMPT.md"
        self.prompt.write_text("Perform the scoped task.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()


    def test_claude_pre_tool_hook_blocks_direct_reads_outside_the_task_workspace(self):
        env = {"NARROW_PROJECT_ROOT": str(self.root), "NARROW_TASK_ID": self.task["id"]}
        hook_input = {
            "session_id": "claude-read-session",
            "tool_name": "Read",
            "tool_input": {"file_path": str(self.root / ".narrow" / "state.sqlite3")},
        }
        with patch.dict(os.environ, env), patch("sys.stdin", BytesInput(hook_input)), patch("sys.stderr", io.StringIO()):
            result = handle_claude_hook("pre-tool-use")
        self.assertEqual(result, 2)
        self.assertEqual(self.store.events(self.task["id"])[-1]["type"], "CLAUDE_FILE_READ_BLOCKED")

    def test_claude_pre_tool_hook_allows_workspace_search_but_blocks_parent_traversal(self):
        env = {"NARROW_PROJECT_ROOT": str(self.root), "NARROW_TASK_ID": self.task["id"]}
        good = {"session_id": "s", "tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}}
        with patch.dict(os.environ, env), patch("sys.stdin", BytesInput(good)), patch("sys.stdout", io.StringIO()):
            self.assertEqual(handle_claude_hook("pre-tool-use"), 0)
        bad = {"session_id": "s", "tool_name": "Glob", "tool_input": {"pattern": "../../**/*"}}
        with patch.dict(os.environ, env), patch("sys.stdin", BytesInput(bad)), patch("sys.stderr", io.StringIO()):
            self.assertEqual(handle_claude_hook("pre-tool-use"), 2)

    def test_claude_adapter_only_enables_subagent_tools_after_explicit_opt_in(self):
        adapter = ClaudeCodeAdapter()
        settings = self.temp_path / "claude-settings.json"
        settings.write_text("{}", encoding="utf-8")
        settings.with_name("claude-mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
        normal = adapter.build_command(workspace=self.workspace, prompt_file=self.prompt, settings_file=settings)
        enabled = adapter.build_command(workspace=self.workspace, prompt_file=self.prompt, settings_file=settings, enable_subagents=True)
        self.assertNotIn("Agent", normal[normal.index("--tools") + 1])
        self.assertIn("Agent", enabled[enabled.index("--tools") + 1])
        self.assertIn("TaskCreate", enabled[enabled.index("--tools") + 1])
        self.assertIn("TaskCompleted", json.dumps(claude_settings_path(self.store, self.task["id"], enable_subagents=True).read_text(encoding="utf-8")))

    def test_claude_task_and_subagent_hooks_store_metadata_not_descriptions_or_errors(self):
        env = {"NARROW_PROJECT_ROOT": str(self.root), "NARROW_TASK_ID": self.task["id"]}
        events = [
            ("task-created", {"task_id": "task-01", "task_subject": "Private subject", "task_description": "secret task description", "agent_id": "agent-01"}),
            ("task-completed", {"task_id": "task-01", "task_subject": "Private subject", "task_description": "secret task description", "teammate_name": "builder"}),
            ("subagent-start", {"agent_id": "agent-02", "agent_type": "Explore"}),
            ("subagent-stop", {"agent_id": "agent-02", "agent_type": "Explore", "last_assistant_message": "private agent output"}),
            ("post-tool-use-failure", {"tool_name": "Bash", "tool_input": {"command": "npm test"}, "error": "private error output containing secret-value", "duration_ms": 123}),
        ]
        for name, extra in events:
            hook_input = {"session_id": "session-safe", "tool_name": extra.get("tool_name", ""), "tool_input": extra.get("tool_input", {}), **extra}
            with patch.dict(os.environ, env), patch("sys.stdin", BytesInput(hook_input)), patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
                self.assertEqual(handle_claude_hook(name), 0)
        journal = json.dumps(self.store.events(self.task["id"], limit=100))
        self.assertIn("CLAUDE_TASK_CREATED", journal)
        self.assertIn("CLAUDE_TASK_COMPLETED", journal)
        self.assertIn("CLAUDE_SUBAGENT_STARTED", journal)
        self.assertIn("CLAUDE_SUBAGENT_STOPPED", journal)
        self.assertIn("CLAUDE_TOOL_FAILED", journal)
        self.assertNotIn("secret task description", journal)
        self.assertNotIn("private agent output", journal)
        self.assertNotIn("private error output", journal)

    def test_claude_settings_network_allowlist_is_explicit_and_validated(self):
        config_path = self.root / ".narrow" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["claude_network_allowed_domains"] = ["registry.npmjs.org", "*.pythonhosted.org:443"]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        path = claude_settings_path(self.store, self.task["id"])
        settings = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(settings["sandbox"]["network"]["allowedDomains"], ["registry.npmjs.org", "*.pythonhosted.org:443"])
        config["claude_network_allowed_domains"] = ["https://evil.example/path"]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(Exception, "Invalid Claude sandbox network domain"):
            claude_settings_path(self.store, self.task["id"])

    def test_claude_adapter_uses_print_stream_json_and_denies_approval_prompts(self):
        adapter = ClaudeCodeAdapter()
        settings = self.temp_path / "claude-settings.json"
        settings.write_text("{}", encoding="utf-8")
        settings.with_name("claude-mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
        command = adapter.build_command(
            workspace=self.workspace,
            prompt_file=self.prompt,
            session_id="session-123",
            model="sonnet",
            max_turns=17,
            settings_file=settings,
        )
        self.assertEqual(command[0], "claude")
        self.assertIn("--print", command)
        self.assertIn("stream-json", command)
        self.assertIn("--permission-mode", command)
        self.assertIn("default", command)
        self.assertIn("--permission-prompts", command)
        self.assertIn("none", command)
        self.assertIn("--resume", command)
        self.assertIn("session-123", command)
        self.assertIn("--max-turns", command)
        self.assertIn("17", command)
        self.assertEqual(command[-1], "Follow the complete NARROW task instructions supplied on standard input.")
        self.assertNotIn(self.prompt.read_text(encoding="utf-8"), command)
        self.assertIn("--input-format", command)
        self.assertIn("text", command)
        self.assertFalse(any(arg in command for arg in ("--dangerously-skip-permissions", "--yolo")))

    def test_codex_adapter_uses_workspace_write_and_fail_closed_noninteractive_approvals(self):
        adapter = CodexCliAdapter()
        command = adapter.build_command(
            workspace=self.workspace,
            prompt_file=self.prompt,
            session_id="thread-123",
            model="gpt-5-codex",
        )
        self.assertEqual(command[:4], ["codex", "exec", "resume", "thread-123"])
        self.assertIn("--json", command)
        self.assertIn("--cd", command)
        self.assertIn(str(self.workspace), command)
        self.assertIn("--sandbox", command)
        self.assertIn("workspace-write", command)
        self.assertIn("--ask-for-approval", command)
        self.assertIn("never", command)
        self.assertEqual(command[-1], "-")
        self.assertFalse(any(arg in command for arg in ("--dangerously-bypass-approvals-and-sandbox", "--yolo")))

    def test_claude_adapter_can_apply_optional_client_side_budget(self):
        adapter = ClaudeCodeAdapter()
        command = adapter.build_command(
            workspace=self.workspace,
            prompt_file=self.prompt,
            max_budget_usd=3.25,
        )
        self.assertIn("--max-budget-usd", command)
        self.assertIn("3.25", command)

    def test_claude_ultracode_requires_subagents_and_uses_provider_mode(self):
        adapter = ClaudeCodeAdapter()
        with self.assertRaisesRegex(Exception, "requires explicit"):
            adapter.build_command(workspace=self.workspace, prompt_file=self.prompt, ultracode=True)
        command = adapter.build_command(
            workspace=self.workspace, prompt_file=self.prompt,
            enable_subagents=True, ultracode=True,
        )
        self.assertEqual(command[command.index("--effort") + 1], "ultracode")
        with self.assertRaisesRegex(Exception, "Do not combine"):
            adapter.build_command(
                workspace=self.workspace, prompt_file=self.prompt,
                enable_subagents=True, ultracode=True, effort="high",
            )

    def test_claude_effort_is_passed_only_when_explicit_and_validated(self):
        adapter = ClaudeCodeAdapter()
        normal = adapter.build_command(workspace=self.workspace, prompt_file=self.prompt)
        high = adapter.build_command(workspace=self.workspace, prompt_file=self.prompt, effort="xhigh")
        self.assertNotIn("--effort", normal)
        self.assertEqual(high[high.index("--effort") + 1], "xhigh")
        with self.assertRaisesRegex(Exception, "effort"):
            adapter.build_command(workspace=self.workspace, prompt_file=self.prompt, effort='high --dangerously-skip-permissions')

    def test_codex_effort_uses_validated_config_override(self):
        command = CodexCliAdapter().build_command(workspace=self.workspace, prompt_file=self.prompt, effort="high")
        self.assertEqual(command[command.index("--config") + 1], 'model_reasoning_effort="high"')
        with self.assertRaisesRegex(Exception, "reasoning effort"):
            CodexCliAdapter().build_command(workspace=self.workspace, prompt_file=self.prompt, effort="max")

    def test_codex_adapter_rejects_unsupported_narrow_budget(self):
        with self.assertRaisesRegex(Exception, "does not expose"):
            CodexCliAdapter().build_command(
                workspace=self.workspace,
                prompt_file=self.prompt,
                max_budget_usd=5.0,
            )

    def test_provider_status_does_not_execute_cli_unless_version_probe_is_requested(self):
        with patch("narrow.providers.shutil.which", side_effect=["/bin/claude", None]), patch("narrow.providers.subprocess.run") as run:
            status = provider_status()
        self.assertTrue(status[0]["available"])
        self.assertEqual(status[0]["path"], "/bin/claude")
        self.assertIsNone(status[0]["version"])
        self.assertFalse(status[1]["available"])
        run.assert_not_called()

    def test_provider_version_probe_uses_only_version_flag(self):
        completed = subprocess.CompletedProcess(["/bin/claude", "--version"], 0, "2.1.300\n", "")
        with patch("narrow.providers.shutil.which", side_effect=["/bin/claude", "/bin/codex"]), patch("narrow.providers.subprocess.run", return_value=completed) as run:
            status = provider_status(probe_versions=True)
        self.assertEqual(status[0]["version"], "2.1.300")
        self.assertEqual(status[0]["compatibility"], "compatible")
        self.assertEqual(status[0]["minimum_version"], "2.1.290")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], ["/bin/claude", "--version"])
        self.assertEqual(run.call_args_list[1].args[0], ["/bin/codex", "--version"])

    def test_provider_status_recommends_claude_upgrade_below_telemetry_minimum(self):
        completed = subprocess.CompletedProcess(["/bin/claude", "--version"], 0, "2.1.250\n", "")
        with patch("narrow.providers.shutil.which", side_effect=["/bin/claude", None]), patch("narrow.providers.subprocess.run", return_value=completed):
            status = provider_status(probe_versions=True)
        self.assertEqual(status[0]["compatibility"], "upgrade_recommended")
        self.assertIn("2.1.290", status[0]["compatibility_note"])

    def test_claude_event_parser_does_not_persist_message_content(self):
        adapter = ClaudeCodeAdapter()
        secret_text = "This message contains api_key=should-not-be-stored"
        parsed = adapter.parse_event({
            "type": "system",
            "subtype": "init",
            "session_id": "s-123",
            "model": "claude-sonnet",
            "cwd": "/tmp/work",
            "tools": ["Read", "Edit"],
        })
        self.assertEqual(parsed.event_type, "PROVIDER_SESSION_STARTED")
        self.assertEqual(parsed.session_id, "s-123")
        assistant = adapter.parse_event({
            "type": "assistant",
            "session_id": "s-123",
            "message": {"content": [{"type": "text", "text": secret_text}, {"type": "tool_use", "name": "Edit", "input": {"file_path": "/secret"}}]},
        })
        self.assertEqual(assistant.payload["tools"], ["Edit"])
        self.assertNotIn(secret_text, json.dumps(assistant.payload))
        self.assertNotIn("/secret", json.dumps(assistant.payload))

    def test_codex_event_parser_only_keeps_safe_event_metadata(self):
        adapter = CodexCliAdapter()
        parsed = adapter.parse_event({
            "type": "thread.started",
            "thread_id": "thread-456",
            "prompt": "private prompt text",
        })
        self.assertEqual(parsed.event_type, "PROVIDER_SESSION_STARTED")
        self.assertEqual(parsed.session_id, "thread-456")
        self.assertNotIn("private prompt text", json.dumps(parsed.payload))

    def test_latest_provider_session_rejects_untrusted_argument_like_values(self):
        self.store.add_event(self.task["id"], "PROVIDER_SESSION_STARTED", {"provider": "claude", "session_id": "--yolo"})
        self.assertIsNone(latest_provider_session_id(self.store, self.task["id"], "claude"))

    def test_assistant_text_is_shown_to_user_but_not_persisted(self):
        callback = make_event_callback(self.store, self.task["id"], ClaudeCodeAdapter())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            callback({
                "type": "assistant",
                "session_id": "session-321",
                "message": {"content": [{"type": "text", "text": "Finished. api_key=secret-value"}]},
            })
        self.assertIn("Finished.", output.getvalue())
        journal = json.dumps(self.store.events(self.task["id"]))
        self.assertNotIn("secret-value", journal)
        self.assertNotIn("Finished.", journal)

    def test_latest_provider_session_is_provider_specific(self):
        self.store.add_event(self.task["id"], "PROVIDER_SESSION_STARTED", {"provider": "claude", "session_id": "claude-1"})
        self.store.add_event(self.task["id"], "PROVIDER_SESSION_STARTED", {"provider": "codex", "session_id": "codex-1"})
        self.store.add_event(self.task["id"], "PROVIDER_SESSION_STARTED", {"provider": "claude", "session_id": "claude-2"})
        self.assertEqual(latest_provider_session_id(self.store, self.task["id"], "claude"), "claude-2")
        self.assertEqual(latest_provider_session_id(self.store, self.task["id"], "codex"), "codex-1")

    def test_claude_settings_install_fail_closed_direct_file_hook_and_valid_command_shape(self):
        settings_path = claude_settings_path(self.store, self.task["id"])
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        pre_tool = settings["hooks"]["PreToolUse"][0]
        self.assertEqual(pre_tool["matcher"], "Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit")
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertTrue(settings["sandbox"]["failIfUnavailable"])
        self.assertFalse(settings["sandbox"]["allowUnsandboxedCommands"])
        self.assertTrue(settings["sandbox"]["network"]["strictAllowlist"])
        self.assertEqual(settings["sandbox"]["network"]["allowedDomains"], [])
        self.assertTrue(settings["permissions"]["blockReadsOutsideWorkingDirectories"])
        self.assertTrue((settings_path.parent / "claude-mcp.json").is_file())
        hook = pre_tool["hooks"][0]
        self.assertEqual(hook["onFailure"], "block")
        self.assertIsInstance(hook["command"], str)
        self.assertIn("pre-tool-use", hook["command"])
        self.assertNotIn("args", hook)

    def test_claude_settings_register_modern_subagent_task_and_failure_hooks(self):
        settings_path = claude_settings_path(self.store, self.task["id"], enable_subagents=True)
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        for name in ("PostToolUseFailure", "SubagentStart", "SubagentStop", "TaskCreated", "TaskCompleted"):
            self.assertIn(name, settings["hooks"])
        for name in ("SubagentStart", "SubagentStop", "TaskCreated", "TaskCompleted"):
            self.assertEqual(settings["hooks"][name][0]["hooks"][0]["onFailure"], "continue")

    def test_direct_write_path_gate_allows_only_in_scope_paths(self):
        good = _validate_direct_write(self.workspace, "src/new.py")
        outside = _validate_direct_write(self.workspace, "README.md")
        reserved = _validate_direct_write(self.workspace, "NARROW_TASK.md")
        traversal = _validate_direct_write(self.workspace, "../outside.txt")
        self.assertTrue(good[0], good)
        self.assertEqual(good[1], "src/new.py")
        self.assertTrue(outside[0], outside)  # path-safe, but not authorized by the allowlist
        scope = classify_paths([outside[1]], self.task["allowed"], self.task["denied"])
        self.assertEqual(scope["outside"], ["README.md"])
        self.assertFalse(reserved[0])
        self.assertFalse(traversal[0])

    def test_direct_write_path_gate_rejects_symlinked_parent_and_hardlink(self):
        external = self.temp_path / "external"
        external.mkdir()
        (self.workspace / "src" / "linked-dir" ).symlink_to(external, target_is_directory=True)
        symlink_result = _validate_direct_write(self.workspace, "src/linked-dir/new.py")
        self.assertFalse(symlink_result[0])
        self.assertIn("symlink", symlink_result[2])

        outside_file = self.temp_path / "outside-file.py"
        outside_file.write_text("DO_NOT_TOUCH = True\n", encoding="utf-8")
        hard_target = self.workspace / "src" / "hardlinked.py"
        try:
            os.link(outside_file, hard_target)
        except OSError as exc:
            self.skipTest(f"Hard links are unavailable in this environment: {exc}")
        hardlink_result = _validate_direct_write(self.workspace, "src/hardlinked.py")
        self.assertFalse(hardlink_result[0])
        self.assertIn("hardlink", hardlink_result[2])
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "DO_NOT_TOUCH = True\n")

    def test_pre_tool_use_hook_blocks_out_of_scope_file_and_records_event(self):
        env = {
            "NARROW_PROJECT_ROOT": str(self.root),
            "NARROW_TASK_ID": self.task["id"],
        }
        hook_input = {
            "session_id": "claude-test-session",
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.workspace / "README.md")},
        }
        with patch.dict(os.environ, env), patch("sys.stdin", BytesInput(hook_input)), patch("sys.stderr", io.StringIO()):
            result = handle_claude_hook("pre-tool-use")
        self.assertEqual(result, 2)
        self.assertEqual(self.store.events(self.task["id"])[-1]["type"], "CLAUDE_FILE_WRITE_BLOCKED")

    def test_pre_tool_use_hook_allows_in_scope_file_and_logs_only_path_metadata(self):
        env = {
            "NARROW_PROJECT_ROOT": str(self.root),
            "NARROW_TASK_ID": self.task["id"],
        }
        hook_input = {
            "session_id": "claude-test-session",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.workspace / "src" / "app.py"), "new_string": "PRIVATE CONTENT"},
        }
        with patch.dict(os.environ, env), patch("sys.stdin", BytesInput(hook_input)), patch("sys.stdout", io.StringIO()):
            result = handle_claude_hook("pre-tool-use")
        self.assertEqual(result, 0)
        event = self.store.events(self.task["id"])[-1]
        self.assertEqual(event["type"], "CLAUDE_FILE_WRITE_ALLOWED")
        self.assertNotIn("PRIVATE CONTENT", json.dumps(event["payload"]))

    def test_agent_jsonl_output_is_parsed_without_leaking_raw_event_to_journal(self):
        events: list[dict] = []
        code = (
            "import json; "
            "print(json.dumps({'type':'thread.started','thread_id':'thread-789'})); "
            "print(json.dumps({'type':'item.started','item':{'type':'tool_call','name':'read_file','input':{'path':'private'}}}))"
        )
        result = run_agent(
            self.store,
            self.task["id"],
            [sys.executable, "-c", code],
            event_callback=lambda event: events.append(event),
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["thread_id"], "thread-789")
        self.assertEqual(events[1]["item"]["name"], "read_file")

    def test_supervisor_sends_long_task_prompt_through_stdin_not_argv_or_journal(self):
        events: list[dict] = []
        private_prompt = "private task context that must not enter the journal"
        code = (
            "import json,sys; payload=sys.stdin.read(); "
            "print(json.dumps({'type':'thread.started','thread_id':'thread-stdin'})); "
            "print('received-bytes='+str(len(payload)))"
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = run_agent(
                self.store,
                self.task["id"],
                [sys.executable, "-c", code],
                event_callback=lambda event: events.append(event),
                stdin_text=private_prompt * 1000,
            )
        self.assertEqual(result, 0)
        self.assertEqual(events[0]["thread_id"], "thread-stdin")
        self.assertIn("received-bytes=", output.getvalue())
        journal = json.dumps(self.store.events(self.task["id"]))
        self.assertNotIn("private task context", journal)
        self.assertIn('"byte_length":', journal)

    def test_work_receipt_is_snapshot_bound_and_omits_raw_provider_ids(self):
        self.store.add_event(self.task["id"], "PROVIDER_SESSION_STARTED", {
            "provider": "claude", "session_id": "private-session-id", "model": "sonnet"
        })
        receipt = _work_receipt(self.store, self.store.get_task(self.task["id"]))
        markdown = _render_work_receipt_markdown(receipt)
        self.assertEqual(receipt["schema"], "narrow.work-receipt.v1")
        self.assertIn("workspace", receipt)
        self.assertNotIn("private-session-id", json.dumps(receipt))
        self.assertIn("Work Receipt", markdown)
        self.assertIn("Original checkout unchanged", markdown)

    def test_report_subcommand_is_available(self):
        args = build_parser().parse_args(["report", "--format", "json"])
        self.assertEqual(args.format, "json")

    def test_drive_parser_accepts_native_providers_without_a_configured_agent(self):
        args = build_parser().parse_args(["drive", "--provider", "claude", "--max-turns", "30", "--max-budget-usd", "2.5"])
        self.assertEqual(args.provider, "claude")
        self.assertIsNone(args.agent)
        self.assertEqual(args.max_turns, 30)
        self.assertEqual(args.max_budget_usd, 2.5)
        self.assertIsNone(args.effort)
        self.assertFalse(args.allow_subagents)
        effort_args = build_parser().parse_args(["drive", "--provider", "codex", "--effort", "xhigh"])
        self.assertEqual(effort_args.effort, "xhigh")
        opted_in = build_parser().parse_args(["drive", "--provider", "claude", "--allow-subagents"])
        self.assertTrue(opted_in.allow_subagents)
        ultracode = build_parser().parse_args(["drive", "--provider", "claude", "--allow-subagents", "--ultracode"])
        self.assertTrue(ultracode.ultracode)
        provider_args = build_parser().parse_args(["providers", "--versions", "--json"])
        self.assertTrue(provider_args.versions)
        self.assertTrue(provider_args.json)
        doctor_args = build_parser().parse_args(["doctor", "--versions"])
        self.assertTrue(doctor_args.versions)
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["drive"])


if __name__ == "__main__":
    unittest.main()
