from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.core import NarrowError
from narrow.egress_proxy import ProxyPolicyError
from narrow.credential_broker import CredentialLease
from narrow.agent_runtime import (
    build_docker_agent_command,
    normalize_provider_argv,
    validate_agent_image,
    validate_claude_container_settings,
)


class AgentRuntimePlanTests(unittest.TestCase):
    def test_accepts_digest_pinned_image(self):
        value = "ghcr.io/acme/narrow-agent@sha256:" + "a" * 64
        self.assertEqual(validate_agent_image(value), value)

    def test_accepts_port_qualified_local_registry_digest(self):
        value = "localhost:5000/narrow-agent@sha256:" + "c" * 64
        self.assertEqual(validate_agent_image(value), value)

    def test_rejects_mutable_image_tags(self):
        with self.assertRaises(NarrowError):
            validate_agent_image("narrow-agent:latest")

    def test_rewrites_codex_workspace(self):
        command = ["codex", "exec", "--json", "--cd", "/tmp/task", "--sandbox", "workspace-write", "--ask-for-approval", "never", "-"]
        result = normalize_provider_argv("codex", command)
        self.assertEqual(result[result.index("--cd") + 1], "/workspace")

    def test_rewrites_codex_resume(self):
        command = ["codex", "exec", "resume", "thread_123", "--json", "--cd", "/tmp/task", "--sandbox", "workspace-write", "--ask-for-approval", "never", "-"]
        result = normalize_provider_argv("codex", command)
        self.assertEqual(result[2:4], ["resume", "thread_123"])

    def test_allows_only_validated_effort_setting(self):
        command = ["codex", "exec", "--json", "--cd", "/tmp/task", "--sandbox", "workspace-write", "--ask-for-approval", "never", "--config", 'model_reasoning_effort="high"', "-"]
        result = normalize_provider_argv("codex", command)
        self.assertIn('model_reasoning_effort="high"', result)

    def test_rejects_unreviewed_config_setting(self):
        command = ["codex", "exec", "--json", "--cd", "/tmp/task", "--sandbox", "workspace-write", "--ask-for-approval", "never", "--config", "other_setting=true", "-"]
        with self.assertRaises(NarrowError):
            normalize_provider_argv("codex", command)

    def test_rewrites_claude_task_settings_to_fixed_container_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = root / "claude-settings.json"
            mcp = root / "claude-mcp.json"
            settings.write_text(json.dumps({
                "permissions": {"blockReadsOutsideWorkingDirectories": True},
                "hooks": {
                    "PreToolUse": [{
                        "matcher": "Read|Write",
                        "hooks": [{
                            "type": "command",
                            "command": "python -m narrow hook claude pre-tool-use",
                            "timeout": 5,
                            "onFailure": "block",
                        }],
                    }],
                },
            }), encoding="utf-8")
            mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
            validate_claude_container_settings(settings, mcp)
            command = [
                "claude", "--print", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "default", "--permission-prompts", "none",
                "--max-turns", "40", "--input-format", "text",
                "--tools", "Bash,Read,Edit,Write,Glob,Grep,NotebookEdit",
                "--disallowedTools", "mcp__*",
                "--settings", str(settings), "--strict-mcp-config",
                "--mcp-config", str(mcp),
                "Follow the complete NARROW task instructions supplied on standard input.",
            ]
            result = normalize_provider_argv("claude", command)
            self.assertEqual(result[result.index("--settings") + 1], "/run/narrow/claude-settings.json")
            self.assertEqual(result[result.index("--mcp-config") + 1], "/run/narrow/claude-mcp.json")
            self.assertIn("--strict-mcp-config", result)
            self.assertNotIn(str(root), " ".join(result))

    def test_claude_container_rejects_host_sandbox_policy_or_nonempty_mcp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = root / "settings.json"
            mcp = root / "mcp.json"
            settings.write_text(json.dumps({
                "sandbox": {"enabled": True},
                "permissions": {"blockReadsOutsideWorkingDirectories": True},
                "hooks": {"PreToolUse": []},
            }), encoding="utf-8")
            mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
            with self.assertRaisesRegex(NarrowError, "delegate containment"):
                validate_claude_container_settings(settings, mcp)
            settings.write_text(json.dumps({
                "permissions": {"blockReadsOutsideWorkingDirectories": True},
                "hooks": {"PreToolUse": []},
            }), encoding="utf-8")
            mcp.write_text(json.dumps({"mcpServers": {"local": {"command": "python"}}}), encoding="utf-8")
            with self.assertRaisesRegex(NarrowError, "exactly empty"):
                validate_claude_container_settings(settings, mcp)

    def test_rejects_missing_prompt_sentinel(self):
        command = ["codex", "exec", "--json", "--cd", "/tmp/task", "--sandbox", "workspace-write", "--ask-for-approval", "never"]
        with self.assertRaises(NarrowError):
            normalize_provider_argv("codex", command)



    @unittest.skipIf(os.name == "nt" or os.getuid() == 0, "Requires a non-root POSIX test user")
    def test_builds_restricted_agent_docker_argv(self):
        task_id = "task_123"
        execution_id = "123e4567-e89b-12d3-a456-426614174000"
        image = "ghcr.io/acme/narrow-agent@sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / ".narrow"
            credential_dir = state / "runtime" / "agent" / task_id / execution_id / "credentials"
            credential_dir.mkdir(parents=True)
            secret = credential_dir / ("codex-" + "c" * 32 + ".secret")
            payload = b"test-api-key"
            secret.write_bytes(payload)
            secret.chmod(0o400)
            lease = CredentialLease(
                path=secret,
                state_dir=state,
                provider="codex",
                environment_name="OPENAI_API_KEY",
                task_id=task_id,
                execution_id=execution_id,
                expires_at=time.time() + 300,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            proxy_token = state / "runtime" / "agent" / task_id / execution_id / "proxy.token"
            proxy_token.parent.mkdir(parents=True, exist_ok=True)
            proxy_token.write_text("d" * 48 + "\n", encoding="ascii")
            proxy_token.chmod(0o400)
            workspace = root / "workspace"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            manifest = root / "policy.json"
            manifest.write_text("{}", encoding="utf-8")
            provider = [
                "codex", "exec", "--json", "--cd", str(workspace),
                "--sandbox", "workspace-write", "--ask-for-approval", "never", "-",
            ]
            with patch("narrow.agent_runtime.read_policy_manifest"):
                command = build_docker_agent_command(
                    image, provider,
                    workspace_path=workspace,
                    task_home_path=home,
                    policy_manifest_path=manifest,
                    credential_lease=lease,
                    task_id=task_id,
                    execution_id=execution_id,
                    proxy_token_path=proxy_token,
                    uid=os.getuid(),
                    gid=os.getgid(),
                )
        self.assertEqual(command[:2], ["docker", "run"])
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--network") + 1],
                         "narrow-agent-task_123-123e4567e89b")
        self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
        self.assertEqual(command[command.index("--security-opt") + 1], "no-new-privileges:true")
        self.assertIn("target=/run/narrow/policy.json,readonly", " ".join(command))
        self.assertIn("target=/run/secrets/provider-api-key,readonly", " ".join(command))
        self.assertIn("target=/run/secrets/proxy-token,readonly", " ".join(command))
        self.assertFalse(any("HTTP_PROXY=" in value or "HTTPS_PROXY=" in value for value in command))
        self.assertIn("--proxy-token-file", command)
        self.assertEqual(command[-1], "-")
        self.assertEqual(command[command.index("--workdir") + 1], "/workspace")
        self.assertEqual(command[command.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")

    @unittest.skipIf(os.name == "nt" or os.getuid() == 0, "Requires a non-root POSIX test user")
    def test_builds_restricted_claude_agent_docker_argv(self):
        task_id = "task_claude_123"
        execution_id = "123e4567-e89b-12d3-a456-426614174000"
        image = "ghcr.io/acme/narrow-agent@sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / ".narrow"
            credential_dir = state / "runtime" / "agent" / task_id / execution_id / "credentials"
            credential_dir.mkdir(parents=True)
            secret = credential_dir / ("claude-" + "c" * 32 + ".secret")
            payload = b"test-anthropic-key"
            secret.write_bytes(payload)
            secret.chmod(0o400)
            lease = CredentialLease(
                path=secret,
                state_dir=state,
                provider="claude",
                environment_name="ANTHROPIC_API_KEY",
                task_id=task_id,
                execution_id=execution_id,
                expires_at=time.time() + 300,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            proxy_token = state / "runtime" / "agent" / task_id / execution_id / "proxy.token"
            proxy_token.parent.mkdir(parents=True, exist_ok=True)
            proxy_token.write_text("d" * 48 + "\\n", encoding="ascii")
            proxy_token.chmod(0o400)
            workspace = root / "workspace"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            manifest = root / "policy.json"
            manifest.write_text("{}", encoding="utf-8")
            settings_dir = state / "providers" / task_id
            settings_dir.mkdir(parents=True)
            claude_settings = settings_dir / "claude-settings.json"
            claude_mcp = settings_dir / "claude-mcp.json"
            claude_settings.write_text(json.dumps({
                "permissions": {"blockReadsOutsideWorkingDirectories": True},
                "hooks": {
                    "PreToolUse": [{
                        "matcher": "Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit",
                        "hooks": [{
                            "type": "command",
                            "command": "python -m narrow hook claude pre-tool-use",
                            "timeout": 5,
                            "onFailure": "block",
                        }],
                    }],
                },
            }), encoding="utf-8")
            claude_mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
            provider = [
                "claude", "--print", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "default", "--permission-prompts", "none",
                "--max-turns", "40", "--input-format", "text",
                "--tools", "Bash,Read,Edit,Write,Glob,Grep,NotebookEdit",
                "--disallowedTools", "mcp__*",
                "--settings", str(claude_settings), "--strict-mcp-config",
                "--mcp-config", str(claude_mcp),
                "Follow the complete NARROW task instructions supplied on standard input.",
            ]
            with patch("narrow.agent_runtime.read_policy_manifest"):
                command = build_docker_agent_command(
                    image, provider,
                    workspace_path=workspace,
                    task_home_path=home,
                    policy_manifest_path=manifest,
                    credential_lease=lease,
                    task_id=task_id,
                    execution_id=execution_id,
                    proxy_token_path=proxy_token,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    claude_settings_file=claude_settings,
                    claude_mcp_file=claude_mcp,
                )
        self.assertEqual(command[:2], ["docker", "run"])
        self.assertEqual(command[command.index("--network") + 1],
                         "narrow-agent-task_claude_123-123e4567e89b")
        self.assertIn("target=/run/narrow/claude-settings.json,readonly", " ".join(command))
        self.assertIn("target=/run/narrow/claude-mcp.json,readonly", " ".join(command))
        self.assertIn("-m narrow.agent_entrypoint --provider claude", " ".join(command))
        self.assertFalse(any("ANTHROPIC_API_KEY=" in value for value in command))

    @unittest.skipIf(os.name == "nt" or os.getuid() == 0, "Requires a non-root POSIX test user")
    def test_rejects_invalid_egress_capability(self):
        task_id = "task_123"
        execution_id = "123e4567-e89b-12d3-a456-426614174000"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / ".narrow"
            credential_dir = state / "runtime" / "agent" / task_id / execution_id / "credentials"
            credential_dir.mkdir(parents=True)
            secret = credential_dir / ("codex-" + "e" * 32 + ".secret")
            secret.write_bytes(b"key")
            secret.chmod(0o400)
            lease = CredentialLease(
                path=secret, state_dir=state, provider="codex",
                environment_name="OPENAI_API_KEY", task_id=task_id,
                execution_id=execution_id, expires_at=time.time() + 300,
                sha256=hashlib.sha256(b"key").hexdigest(),
            )
            proxy_token = state / "runtime" / "agent" / task_id / execution_id / "proxy.token"
            proxy_token.write_text("short\n", encoding="ascii")
            proxy_token.chmod(0o400)
            workspace = root / "workspace"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            manifest = root / "policy.json"
            manifest.write_text("{}", encoding="utf-8")
            provider = [
                "codex", "exec", "--json", "--cd", str(workspace),
                "--sandbox", "workspace-write", "--ask-for-approval", "never", "-",
            ]
            with patch("narrow.agent_runtime.read_policy_manifest"):
                with self.assertRaises(ProxyPolicyError):
                    build_docker_agent_command(
                        "ghcr.io/acme/narrow-agent@sha256:" + "f" * 64,
                        provider, workspace_path=workspace, task_home_path=home,
                        policy_manifest_path=manifest, credential_lease=lease,
                        task_id=task_id, execution_id=execution_id, proxy_token_path=proxy_token,
                    )


if __name__ == "__main__":
    unittest.main()
