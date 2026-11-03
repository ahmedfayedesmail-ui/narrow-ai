from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from narrow.core import NarrowError
from narrow.sandbox import prepare_sandbox_command

class SandboxTests(unittest.TestCase):
    def test_off_mode_does_not_wrap_command(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(prepare_sandbox_command(["python", "-m", "pytest"], Path(temp), {"mode": "off"}))

    @patch("narrow.sandbox.subprocess.run")
    @patch("narrow.sandbox.shutil.which", return_value="/usr/bin/docker")
    def test_docker_command_has_isolation_and_limits(self, _which, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as temp:
            plan = prepare_sandbox_command(
                ["python", "-m", "pytest", "-q"], Path(temp),
                {"mode": "docker", "image": "narrow-checks:local", "memory": "1g", "cpus": 1.5, "pids_limit": 128},
            )
        self.assertIn("--network=none", plan.command)
        self.assertIn("--read-only", plan.command)
        self.assertIn("--cap-drop=ALL", plan.command)
        self.assertIn("--pids-limit", plan.command)
        self.assertIn("--memory", plan.command)
        self.assertIn("--cpus", plan.command)
        self.assertIn("--mount", plan.command)
        self.assertEqual(plan.command[-4:], ["python", "-m", "pytest", "-q"])

    def test_rejects_invalid_or_missing_image(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("narrow.sandbox.shutil.which", return_value="/usr/bin/docker"):
                with self.assertRaises(NarrowError):
                    prepare_sandbox_command(["pytest"], Path(temp), {"mode": "docker"})
                with self.assertRaises(NarrowError):
                    prepare_sandbox_command(["pytest"], Path(temp), {"mode": "docker", "image": "bad image"})

    @patch("narrow.sandbox.shutil.which", return_value=None)
    def test_fails_closed_if_engine_is_missing(self, _which):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(NarrowError, "not found on PATH"):
                prepare_sandbox_command(["pytest"], Path(temp), {"mode": "auto", "image": "narrow-checks:local"})

if __name__ == "__main__":
    unittest.main()
