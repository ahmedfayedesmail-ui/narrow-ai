from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from narrow.core import NarrowError
from narrow.container_lifecycle import (
    agent_container_label_args,
    agent_container_labels,
    cleanup_execution_agent_containers,
    list_execution_agent_containers,
)

TASK = "task_123"
EXECUTION = "12345678-1234-4234-8234-123456789abc"
CONTAINER = "a" * 64


def inspected(labels=None, *, status="running"):
    return [{
        "Id": CONTAINER,
        "Name": "/narrow-agent-test",
        "Config": {"Labels": labels if labels is not None else agent_container_labels(TASK, EXECUTION)},
        "State": {"Status": status},
    }]


def run_result(stdout="", returncode=0, stderr=""):
    class Result:
        pass
    result = Result()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


class AgentContainerLifecycleTests(unittest.TestCase):
    def mock_engine_calls(self, inspect_payload=None, ids=CONTAINER):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[1:3] == ["container", "ls"]:
                return run_result(ids + "\n")
            if command[1:3] == ["container", "inspect"]:
                payload = inspect_payload if inspect_payload is not None else inspected()
                return run_result(json.dumps(payload))
            if command[1:4] == ["container", "rm", "--force"]:
                return run_result()
            raise AssertionError(f"Unexpected engine command: {command}")

        which_patch = patch("narrow.container_lifecycle.shutil.which", side_effect=lambda name: f"/usr/bin/{name}")
        run_patch = patch("narrow.container_lifecycle.subprocess.run", side_effect=fake_run)
        which_patch.start()
        run_patch.start()
        self.addCleanup(which_patch.stop)
        self.addCleanup(run_patch.stop)
        return calls

    def test_agent_container_labels_are_execution_scoped(self):
        labels = agent_container_labels(TASK, EXECUTION)
        self.assertEqual(labels["io.narrow.managed"], "true")
        self.assertEqual(labels["io.narrow.component"], "agent")
        self.assertEqual(labels["io.narrow.task"], TASK)
        self.assertEqual(labels["io.narrow.execution"], EXECUTION)
        args = agent_container_label_args(TASK, EXECUTION)
        self.assertEqual(args.count("--label"), len(labels))

    def test_invalid_execution_identity_is_rejected(self):
        for task, execution in (("../outside", EXECUTION), (TASK, "../other"), (TASK, "not-a-uuid")):
            with self.subTest(task=task, execution=execution):
                with self.assertRaises(NarrowError):
                    agent_container_labels(task, execution)

    def test_listing_requires_exact_container_ownership(self):
        self.mock_engine_calls()
        containers = list_execution_agent_containers("docker", TASK, EXECUTION)
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0].container_id, CONTAINER)
        self.assertEqual(containers[0].task_id, TASK)
        self.assertEqual(containers[0].execution_id, EXECUTION)
        self.assertEqual(containers[0].status, "running")

    def test_listing_refuses_a_container_with_mismatched_execution_label(self):
        labels = agent_container_labels(TASK, EXECUTION)
        labels["io.narrow.execution"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.mock_engine_calls(inspect_payload=inspected(labels))
        with self.assertRaisesRegex(NarrowError, "labels do not match"):
            list_execution_agent_containers("docker", TASK, EXECUTION)

    def test_cleanup_rechecks_ownership_before_removing(self):
        calls = self.mock_engine_calls()
        self.assertEqual(cleanup_execution_agent_containers("docker", TASK, EXECUTION), 1)
        removals = [call for call in calls if call[1:4] == ["container", "rm", "--force"]]
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0][-1], CONTAINER)

    def test_cleanup_never_removes_a_foreign_container(self):
        labels = agent_container_labels(TASK, EXECUTION)
        labels["io.narrow.task"] = "another-task"
        calls = self.mock_engine_calls(inspect_payload=inspected(labels))
        with self.assertRaisesRegex(NarrowError, "labels do not match"):
            cleanup_execution_agent_containers("docker", TASK, EXECUTION)
        self.assertFalse(any(call[1:4] == ["container", "rm", "--force"] for call in calls))

    def test_listing_is_bounded_for_one_execution(self):
        ids = "\n".join(f"{index:064x}" for index in range(33))
        self.mock_engine_calls(ids=ids)
        with self.assertRaisesRegex(NarrowError, "large number"):
            list_execution_agent_containers("docker", TASK, EXECUTION)


if __name__ == "__main__":
    unittest.main()
