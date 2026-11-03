from __future__ import annotations

import json
from unittest.mock import patch

import pytest

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


def mock_engine_calls(monkeypatch, inspect_payload=None, ids=CONTAINER):
    monkeypatch.setattr("narrow.container_lifecycle.shutil.which", lambda name: f"/usr/bin/{name}")
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

    monkeypatch.setattr("narrow.container_lifecycle.subprocess.run", fake_run)
    return calls


def test_agent_container_labels_are_execution_scoped():
    labels = agent_container_labels(TASK, EXECUTION)
    assert labels["io.narrow.managed"] == "true"
    assert labels["io.narrow.component"] == "agent"
    assert labels["io.narrow.task"] == TASK
    assert labels["io.narrow.execution"] == EXECUTION
    args = agent_container_label_args(TASK, EXECUTION)
    assert args.count("--label") == len(labels)


@pytest.mark.parametrize("task,execution", [
    ("../outside", EXECUTION),
    (TASK, "../other"),
    (TASK, "not-a-uuid"),
])
def test_invalid_execution_identity_is_rejected(task, execution):
    with pytest.raises(NarrowError):
        agent_container_labels(task, execution)


def test_listing_requires_exact_container_ownership(monkeypatch):
    calls = mock_engine_calls(monkeypatch)
    containers = list_execution_agent_containers("docker", TASK, EXECUTION)
    assert len(containers) == 1
    assert containers[0].container_id == CONTAINER
    assert containers[0].task_id == TASK
    assert containers[0].execution_id == EXECUTION
    assert containers[0].status == "running"


def test_listing_refuses_a_container_with_mismatched_execution_label(monkeypatch):
    labels = agent_container_labels(TASK, EXECUTION)
    labels["io.narrow.execution"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    mock_engine_calls(monkeypatch, inspect_payload=inspected(labels))
    with pytest.raises(NarrowError, match="labels do not match"):
        list_execution_agent_containers("docker", TASK, EXECUTION)


def test_cleanup_rechecks_ownership_before_removing(monkeypatch):
    calls = mock_engine_calls(monkeypatch)
    assert cleanup_execution_agent_containers("docker", TASK, EXECUTION) == 1
    removals = [call for call in calls if call[1:4] == ["container", "rm", "--force"]]
    assert len(removals) == 1
    assert removals[0][-1] == CONTAINER


def test_cleanup_never_removes_a_foreign_container(monkeypatch):
    labels = agent_container_labels(TASK, EXECUTION)
    labels["io.narrow.task"] = "another-task"
    calls = mock_engine_calls(monkeypatch, inspect_payload=inspected(labels))
    with pytest.raises(NarrowError, match="labels do not match"):
        cleanup_execution_agent_containers("docker", TASK, EXECUTION)
    assert not any(call[1:4] == ["container", "rm", "--force"] for call in calls)


def test_listing_is_bounded_for_one_execution(monkeypatch):
    ids = "\n".join(f"{index:064x}" for index in range(33))
    mock_engine_calls(monkeypatch, ids=ids)
    with pytest.raises(NarrowError, match="large number"):
        list_execution_agent_containers("docker", TASK, EXECUTION)
