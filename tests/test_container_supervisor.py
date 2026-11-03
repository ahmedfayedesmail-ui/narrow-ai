from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.container_supervisor import _docker_environment, _private_execution_dir, run_container_agent
from narrow.core import NarrowError


class ContainerSupervisorTests(unittest.TestCase):
    def test_creates_private_task_execution_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / ".narrow"
            root.mkdir()
            result = _private_execution_dir(root, "task_123", "123e4567-e89b-12d3-a456-426614174000")
            self.assertTrue(result.is_dir())
            if os.name != "nt":
                self.assertEqual(result.stat().st_mode & 0o777, 0o700)

    def test_rejects_invalid_task_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / ".narrow"
            root.mkdir()
            with self.assertRaises(NarrowError):
                _private_execution_dir(root, "../other", "123e4567-e89b-12d3-a456-426614174000")

    def test_docker_environment_is_allowlisted(self):
        with patch.dict(os.environ, {"PATH": "/bin", "DOCKER_HOST": "unix:///tmp/docker.sock", "UNRELATED_TOKEN": "hidden"}, clear=True):
            env = _docker_environment()
        self.assertEqual(env, {"PATH": "/bin", "DOCKER_HOST": "unix:///tmp/docker.sock"})

    @unittest.skipIf(os.name == "nt", "POSIX Docker supervision only")
    def test_rejects_unapproved_volume_syntax_before_start(self):
        with patch("narrow.container_supervisor.subprocess.Popen") as popen:
            with self.assertRaisesRegex(NarrowError, "forbidden host-access"):
                run_container_agent(
                    ["docker", "run", "-v", "/:/workspace"],
                    store=None, credential_lease=None, task_id="task_1",
                    execution_id="123e4567-e89b-12d3-a456-426614174000",
                    stdin_text="prompt",
                )
        popen.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX Docker supervision only")
    def test_rejects_host_env_file_syntax_before_start(self):
        with patch("narrow.container_supervisor.subprocess.Popen") as popen:
            with self.assertRaisesRegex(NarrowError, "forbidden host-access"):
                run_container_agent(
                    ["docker", "run", "--env-file", "/tmp/host.env"],
                    store=None, credential_lease=None, task_id="task_1",
                    execution_id="123e4567-e89b-12d3-a456-426614174000",
                    stdin_text="prompt",
                )
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
