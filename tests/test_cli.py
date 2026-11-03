from __future__ import annotations

import unittest

from narrow.cli import build_parser


class CliTests(unittest.TestCase):
    def test_run_and_resume_accept_agent_command(self):
        parser = build_parser()
        run = parser.parse_args(["run", "--", "claude"])
        resume = parser.parse_args(["resume", "--", "codex"])
        drive = parser.parse_args(["drive", "--agent", "claude", "--max-attempts", "2"])
        self.assertEqual(run.command, ["--", "claude"])
        self.assertEqual(resume.command, ["--", "codex"])
        self.assertEqual(drive.agent, "claude")
        self.assertEqual(drive.max_attempts, 2)
        self.assertFalse(drive.allow_subagents)
        dashboard = parser.parse_args(["dashboard", "--port", "8766"])
        self.assertEqual(dashboard.port, 8766)
        self.assertFalse(dashboard.open)
        tasks = parser.parse_args(["tasks", "--all", "--limit", "25", "--json"])
        self.assertTrue(tasks.all)
        self.assertEqual(tasks.limit, 25)
        self.assertTrue(tasks.json)

    def test_discovery_and_proposal_cli_options(self):
        parser = build_parser()
        discover = parser.parse_args(["discover", "--apply"])
        self.assertTrue(discover.apply)
        self.assertFalse(discover.json)
        proposal = parser.parse_args(["propose", "Fix login timeout", "--ollama", "--model", "llama3:8b"])
        self.assertEqual(proposal.goal, "Fix login timeout")
        self.assertTrue(proposal.ollama)
        self.assertEqual(proposal.model, "llama3:8b")
        drive = parser.parse_args(["drive", "--provider", "my-agent"])
        self.assertEqual(drive.provider, "my-agent")

    def test_start_accepts_repeated_scope_and_acceptance_entries(self):
        parser = build_parser()
        args = parser.parse_args([
            "start", "--title", "A", "--goal", "B",
            "--allow", "src/**", "--allow", "tests/**",
            "--accept", "One", "--accept", "Two",
        ])
        self.assertEqual(args.allow, ["src/**", "tests/**"])
        self.assertEqual(args.accept, ["One", "Two"])


if __name__ == "__main__":
    unittest.main()
