from __future__ import annotations

import hashlib
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
)


class AgentRuntimePlanTests(unittest.TestCase):
    def test_accepts_digest_pinned_image(self):
        value = "ghcr.io/acme/narrow-agent@sha256:" + "a" * 64
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

    def test_refuses_claude_until_profile_exists(self):
        with self.assertRaises(NarrowError):
            normalize_provider_argv("claude", ["claude", "--print"])

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
