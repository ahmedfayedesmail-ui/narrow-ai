from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from narrow.profiles import build_security_posture, build_setup_plan, profile_scope_defaults


class SetupProfileTests(unittest.TestCase):
    def _discovery(self, *, languages=(), top_dirs=()):
        return {
            "languages": list(languages),
            "frameworks": [],
            "checks": [
                {
                    "name": "python",
                    "command": ["python", "-m", "pytest", "-q"],
                    "timeout_seconds": 300,
                    "reason": "pytest was detected",
                }
            ],
            "suggested_allow": ["src/**", "tests/**"],
            "suggested_deny": [".narrow/**", "**/.env", "**/*.pem"],
            "top_level_directories": list(top_dirs),
        }

    def test_auto_profile_selects_web_for_javascript_projects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("src", "app", "tests", "data"):
                (root / name).mkdir()
            plan = build_setup_plan(
                root,
                self._discovery(languages=("JavaScript/TypeScript",), top_dirs=("src", "app", "tests", "data")),
            )
            self.assertEqual(plan["profile"], "web")
            self.assertEqual(plan["allowed_paths"], ["src/**", "app/**", "tests/**"])
            self.assertNotIn("data/**", plan["allowed_paths"])

    def test_auto_profile_selects_data_for_python_notebooks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("src", "notebooks", "pipelines", "data", "tests"):
                (root / name).mkdir()
            plan = build_setup_plan(
                root,
                self._discovery(languages=("Python",), top_dirs=("src", "notebooks", "pipelines", "data", "tests")),
            )
            self.assertEqual(plan["profile"], "data")
            self.assertEqual(plan["allowed_paths"], ["src/**", "tests/**", "notebooks/**", "pipelines/**"])
            self.assertNotIn("data/**", plan["allowed_paths"])

    def test_python_profile_does_not_follow_symlinked_source_directories(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            (root / "tests").mkdir()
            (root / "src").symlink_to(Path(outside), target_is_directory=True)
            plan = build_setup_plan(
                root,
                self._discovery(languages=("Python",), top_dirs=("src", "tests")),
                "python",
            )
            self.assertEqual(plan["allowed_paths"], ["tests/**"])

    def test_profile_scope_defaults_rejects_broad_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / ".narrow").mkdir()
            allowed, denied = profile_scope_defaults(
                root,
                {
                    "name": "python",
                    "allowed_paths": ["src/**", "**", "../**", ".narrow/**", "/etc/**"],
                    "denied_paths": [".narrow/**", "**/.env", "../outside/**"],
                },
            )
            self.assertEqual(allowed, ["src/**"])
            self.assertEqual(denied, [".narrow/**", "**/.env"])

    def test_security_report_never_calls_a_git_copy_a_sandbox(self):
        report = build_security_posture(
            {
                "project_profile": {"name": "web"},
                "verification_sandbox": {"mode": "off", "image": None},
            },
            initialized=True,
        )
        self.assertFalse(report["security_enforcement"]["git_workspace_is_os_sandbox"])
        self.assertIn("OFF", report["security_enforcement"]["verification_runtime"])
        self.assertTrue(report["defaults"]["human_acceptance_required"])
        self.assertEqual(report["project_profile"], "web")

    def test_security_report_distinguishes_verification_container_from_agent_sandbox(self):
        report = build_security_posture(
            {"verification_sandbox": {"mode": "docker", "image": "checks@sha256:" + "a" * 64}},
            initialized=True,
        )
        self.assertIn("verification commands only", report["security_enforcement"]["verification_runtime"])
        self.assertIn("not a microVM", report["security_enforcement"]["codex_container_runtime"].lower())


if __name__ == "__main__":
    unittest.main()
