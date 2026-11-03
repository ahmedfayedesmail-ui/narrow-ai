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
        mount = plan.command[plan.command.index("--mount") + 1]
        self.assertIn(",readonly", mount)
        self.assertEqual(plan.command[plan.command.index("--entrypoint") + 1], "python")
        self.assertEqual(plan.command[-3:], ["-m", "pytest", "-q"])

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
            with self.assertRaisesRegex(NarrowError, "found on PATH"):
                prepare_sandbox_command(["pytest"], Path(temp), {"mode": "auto", "image": "narrow-checks:local"})

    @patch("narrow.sandbox.subprocess.run")
    @patch("narrow.sandbox.shutil.which", return_value="/usr/bin/docker")
    def test_task_scoped_dependency_caches_are_mounted_without_home_access(self, _which, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            workspace = temp_path / "workspace"
            workspace.mkdir()
            plan = prepare_sandbox_command(
                ["python", "-m", "pytest"],
                workspace,
                {"mode": "docker", "image": "narrow-checks:local",
                 "dependency_caches": ["pip", "npm"]},
                cache_root=temp_path / "narrow-cache",
                cache_scope="task_123",
            )
        mounts = [plan.command[i + 1] for i, part in enumerate(plan.command[:-1]) if part == "--mount"]
        self.assertTrue(any("dst=/narrow-cache/pip" in mount and "readonly" not in mount for mount in mounts))
        self.assertTrue(any("dst=/narrow-cache/npm" in mount and "readonly" not in mount for mount in mounts))
        self.assertIn("PIP_CACHE_DIR=/narrow-cache/pip", plan.command)
        self.assertIn("npm_config_cache=/narrow-cache/npm", plan.command)
        self.assertNotIn(str(Path.home()), " ".join(plan.command))



if __name__ == "__main__":
    unittest.main()
