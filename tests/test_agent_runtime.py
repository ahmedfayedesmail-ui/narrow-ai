from __future__ import annotations

import unittest

from narrow.core import NarrowError
from narrow.agent_runtime import normalize_provider_argv, validate_agent_image


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


if __name__ == "__main__":
    unittest.main()
