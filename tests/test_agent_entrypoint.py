from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.core import NarrowError
from narrow.agent_entrypoint import (
    build_exec_argv,
    build_provider_environment,
    read_mounted_credential,
    _route_table_has_default_route,
)


class AgentEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "provider.secret"
        self.path.write_text("test-provider-secret", encoding="utf-8")
        if os.name != "nt":
            self.path.chmod(0o400)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_owner_only_regular_secret(self):
        self.assertEqual(read_mounted_credential(self.path), "test-provider-secret")

    def test_rejects_permissive_secret_file(self):
        if os.name == "nt":
            self.skipTest("POSIX permission mode is not available on Windows")
        self.path.chmod(0o444)
        with self.assertRaisesRegex(NarrowError, "owner-only"):
            read_mounted_credential(self.path)

    def test_rejects_symlink_secret(self):
        link = Path(self.temp.name) / "secret-link"
        link.symlink_to(self.path)
        with self.assertRaises(NarrowError):
            read_mounted_credential(link)

    def test_rejects_multiline_secret(self):
        self.path.chmod(0o600)
        self.path.write_text("one\ntwo", encoding="utf-8")
        self.path.chmod(0o400)
        with self.assertRaisesRegex(NarrowError, "single-line"):
            read_mounted_credential(self.path)

    def test_environment_contains_only_allowlisted_values_and_selected_key(self):
        inherited = {
            "PATH": "/usr/bin",
            "HOME": "/home/narrow",
            "HTTP_PROXY": "http://narrow-egress-proxy:3128",
            "NARROW_TASK_ID": "task_123",
            "AWS_SECRET_ACCESS_KEY": "must-not-pass",
            "GITHUB_TOKEN": "must-not-pass",
            "OPENAI_API_KEY": "other-provider-secret",
        }
        env = build_provider_environment("claude", "claude-secret", inherited, proxy_token="p" * 48)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "claude-secret")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["HTTP_PROXY"], "http://narrow:" + "p" * 48 + "@narrow-egress-proxy:3128")
        self.assertEqual(env["HTTPS_PROXY"], env["HTTP_PROXY"])
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1")

    def test_selected_provider_key_replaces_inherited_value(self):
        env = build_provider_environment(
            "codex", "selected-secret",
            {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "wrong-secret", "OPENAI_API_KEY": "wrong-secret"},
            proxy_token="p" * 48,
        )
        self.assertEqual(env["OPENAI_API_KEY"], "selected-secret")
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_only_matching_provider_executable_is_allowed(self):
        self.assertEqual(build_exec_argv("claude", ["claude", "--print"]), ["claude", "--print"])
        with self.assertRaisesRegex(NarrowError, "exact selected CLI"):
            build_exec_argv("claude", ["codex", "exec"])
        with self.assertRaisesRegex(NarrowError, "exact selected CLI"):
            build_exec_argv("claude", ["/tmp/claude", "--print"])

    def test_rejects_shell_and_empty_arguments(self):
        for command in ([], ["claude", ""], ["sh", "-c", "claude"]):
            with self.subTest(command=command), self.assertRaises(NarrowError):
                build_exec_argv("claude", command)


    def test_agent_network_requires_no_ipv4_or_ipv6_default_route(self):
        safe_ipv4 = "Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 0000000A 00000000 00000001 0 0 0 00FFFFFF\n"
        safe_ipv6 = ""
        self.assertFalse(_route_table_has_default_route(safe_ipv4, safe_ipv6))
        default_ipv4 = safe_ipv4 + "eth0 00000000 01001AAC 00000001 0 0 0 00000000\n"
        self.assertTrue(_route_table_has_default_route(default_ipv4, safe_ipv6))
        default_ipv6 = "0" * 32 + " 00 " + "0" * 32 + " 00 " + "0" * 32 + " 00000000 00000000 00000000 00000001 eth0\n"
        self.assertTrue(_route_table_has_default_route(safe_ipv4, default_ipv6))


    def test_route_table_parser_rejects_malformed_ipv4_flags(self):
        malformed = "Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 00000000 01001AAC not-hex 0 0 0 00000000\n"
        with self.assertRaisesRegex(NarrowError, "IPv4 route table contains malformed"):
            _route_table_has_default_route(malformed, "")


if __name__ == "__main__":
    unittest.main()
