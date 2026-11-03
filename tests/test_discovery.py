from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from narrow.discovery import discover_project, propose_contract

class DiscoveryTests(unittest.TestCase):
    def test_discovers_python_pytest_without_running_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "tests" / "test_login.py").write_text("def test_login(): pass\n", encoding="utf-8")
            (root / "pyproject.toml").write_text('[project]\nname="demo"\ndependencies=["pytest"]\n', encoding="utf-8")
            result = discover_project(root)
            self.assertEqual(result["languages"], ["Python"])
            self.assertEqual(result["checks"][0]["command"], ["python", "-m", "pytest", "-q"])
            self.assertIn("src/**", result["suggested_allow"])

    def test_discovers_javascript_package_script_and_lockfile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9'\n", encoding="utf-8")
            result = discover_project(root)
            self.assertEqual(result["checks"][0]["command"], ["pnpm", "test"])

    def test_does_not_guess_tests_without_test_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
            self.assertEqual(discover_project(root)["checks"], [])

    def test_proposal_is_human_review_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "tests" / "test_app.py").write_text("def test_app(): pass\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
            proposal = propose_contract(root, "Fix the login timeout")
            self.assertTrue(proposal["requires_human_review"])
            self.assertIn("src/**", proposal["allow"])
            self.assertIn(".narrow/**", proposal["deny"])
            self.assertTrue(proposal["warning"].startswith("Proposal only."))

    def test_proposal_rejects_parent_and_root_wide_allow_patterns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            proposal = propose_contract(root, "Fix a bug")
            self.assertNotIn("../**", proposal["allow"])
            self.assertNotIn("**", proposal["allow"])

if __name__ == "__main__":
    unittest.main()
