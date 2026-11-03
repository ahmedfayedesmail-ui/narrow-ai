from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.core import Store, NarrowError
from narrow.container_recovery import recover_agent_execution

TASK = "task_recovery_1"
EXECUTION = "12345678-1234-4234-8234-123456789abc"


class ContainerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "NARROW tests"], check=True)
        self.store = Store(self.root)
        self.store.get_task = lambda task_id: {"id": task_id}
        self.events = []
        self.store.add_event = lambda task_id, event, payload: self.events.append((task_id, event, payload))
        execution_root = self.store.state_dir / "runtime" / "agent" / TASK / EXECUTION
        (execution_root / "home").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_recovers_only_the_selected_execution(self):
        with patch("narrow.container_recovery.cleanup_execution_agent_containers", return_value=1) as cleanup, patch(
            "narrow.container_recovery.list_execution_agent_containers", return_value=[]
        ), patch(
            "narrow.container_recovery.cleanup_execution_egress_proxy_containers", return_value=1
        ) as cleanup_proxy, patch(
            "narrow.container_recovery.list_execution_egress_proxy_containers", return_value=[]
        ), patch(
            "narrow.container_recovery.cleanup_execution_egress_uplink_networks", return_value=1
        ) as cleanup_uplink, patch(
            "narrow.container_recovery.cleanup_execution_agent_networks", return_value=1
        ) as cleanup_network:
            report = recover_agent_execution(self.store, TASK, EXECUTION)
        cleanup.assert_called_once_with("docker", TASK, EXECUTION)
        cleanup_proxy.assert_called_once_with("docker", TASK, EXECUTION)
        cleanup_uplink.assert_called_once_with("docker", TASK, EXECUTION)
        cleanup_network.assert_called_once_with("docker", TASK, EXECUTION)
        self.assertEqual(report["status"], "recovered")
        self.assertEqual(report["removed_containers"], 1)
        self.assertEqual(report["removed_egress_proxy_containers"], 1)
        self.assertEqual(report["removed_egress_uplink_networks"], 1)
        self.assertEqual(report["removed_networks"], 1)
        self.assertEqual(self.events[0][1], "CONTAINER_EXECUTION_RECOVERED")

    def test_refuses_when_owned_container_remains(self):
        with patch("narrow.container_recovery.cleanup_execution_agent_containers", return_value=0), patch(
            "narrow.container_recovery.list_execution_agent_containers", return_value=[object()]
        ), patch(
            "narrow.container_recovery.cleanup_execution_egress_proxy_containers"
        ) as cleanup_proxy, patch(
            "narrow.container_recovery.cleanup_execution_agent_networks"
        ) as cleanup_network:
            with self.assertRaisesRegex(NarrowError, "still present"):
                recover_agent_execution(self.store, TASK, EXECUTION)
        cleanup_proxy.assert_not_called()
        cleanup_network.assert_not_called()

    def test_invalid_task_identity_fails_closed(self):
        with patch("narrow.container_recovery.cleanup_execution_agent_containers") as cleanup:
            with self.assertRaises(NarrowError):
                recover_agent_execution(self.store, "../other", EXECUTION)
        cleanup.assert_not_called()


    def test_recovery_cli_requires_exact_execution_arguments(self):
        from narrow.cli import build_parser, cmd_recover_agent
        args = build_parser().parse_args([
            "recover-agent", "--task", TASK, "--execution", EXECUTION, "--yes"
        ])
        self.assertIs(args.func, cmd_recover_agent)
        self.assertEqual(args.task, TASK)
        self.assertEqual(args.execution, EXECUTION)


if __name__ == "__main__":
    unittest.main()
