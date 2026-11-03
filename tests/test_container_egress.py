from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from narrow.container_egress import (
    EgressProxyRuntime,
    provision_execution_egress_proxy,
    remove_execution_proxy_token_file,
    run_codex_container_agent,
)
from narrow.credential_broker import CredentialLease
from narrow.container_lifecycle import (
    AgentNetwork,
    egress_proxy_container_labels,
)
from narrow.core import NarrowError, Store

TASK = "task_123"
EXECUTION = "123e4567-e89b-12d3-a456-426614174000"
IMAGE = "ghcr.io/example/narrow-proxy@sha256:" + "a" * 64
CONTAINER_ID = "c" * 64


class ContainerEgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "NARROW tests"], check=True)
        self.store = Store(self.root)
        self.store.get_task = lambda task_id: {"id": task_id}
        self.agent_network = AgentNetwork(
            network_id="a" * 64,
            name=f"narrow-agent-{TASK[:32]}-{EXECUTION.replace('-', '')[:12]}",
            task_id=TASK,
            execution_id=EXECUTION,
            driver="bridge",
        )
        self.uplink_network = AgentNetwork(
            network_id="b" * 64,
            name=f"narrow-egress-uplink-{TASK[:24]}-{EXECUTION.replace('-', '')[:12]}",
            task_id=TASK,
            execution_id=EXECUTION,
            driver="bridge",
        )
        self.run_args = None
        self.port_bindings = None

    def tearDown(self):
        self.temp.cleanup()

    def _sidecar_inspect(self, token_path: Path):
        return {
            "Id": CONTAINER_ID,
            "Name": "/narrow-egress-proxy-task_123-123e4567e89b",
            "Config": {
                "Image": IMAGE,
                "Labels": egress_proxy_container_labels(TASK, EXECUTION),
            },
            "State": {"Running": True},
            "HostConfig": {
                "ReadonlyRootfs": True,
                "Privileged": False,
                "CapAdd": None,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "PortBindings": self.port_bindings,
                "NetworkMode": self.agent_network.name,
            },
            "Mounts": [{
                "Source": str(token_path.resolve()),
                "Destination": "/run/secrets/proxy-token",
                "RW": False,
            }],
            "NetworkSettings": {
                "Networks": {
                    self.agent_network.name: {"Aliases": ["narrow-egress-proxy"]},
                    self.uplink_network.name: {"Aliases": ["narrow-egress-uplink"]},
                }
            },
        }

    def _patch_provisioner(self):
        patches = [
            patch("narrow.container_egress.list_execution_egress_proxy_containers", return_value=[]),
            patch("narrow.container_egress.list_execution_agent_networks", return_value=[]),
            patch("narrow.container_egress.list_execution_egress_uplink_networks", return_value=[]),
            patch("narrow.container_egress.create_execution_agent_network", return_value=self.agent_network),
            patch("narrow.container_egress.create_execution_egress_uplink_network", return_value=self.uplink_network),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        def fake_docker(args, *, timeout=20.0):
            if args == ["context", "show"]:
                return "default"
            if args and args[0] == "run":
                self.run_args = list(args)
                mount = next(item for item in args if item.startswith("type=bind,source="))
                source = mount.split("source=", 1)[1].split(",target=", 1)[0]
                self.token_path = Path(source)
                return CONTAINER_ID
            if args[:2] == ["network", "connect"]:
                return ""
            if args[:2] == ["container", "inspect"]:
                return json.dumps([self._sidecar_inspect(self.token_path)])
            if args[:2] == ["exec", CONTAINER_ID]:
                return ""
            if args[:3] == ["container", "rm", "--force"]:
                return ""
            raise AssertionError("Unexpected Docker argv: " + repr(args))

        docker_patch = patch("narrow.container_egress._docker", side_effect=fake_docker)
        docker_patch.start()
        self.addCleanup(docker_patch.stop)

    @unittest.skipIf(os.name == "nt" or os.getuid() == 0, "Requires a non-root POSIX host identity")
    def test_provisions_unpublished_dual_homed_proxy_sidecar(self):
        self._patch_provisioner()
        runtime = provision_execution_egress_proxy(
            self.store, TASK, EXECUTION, IMAGE, ["api.openai.com"]
        )
        self.assertEqual(runtime.container_id, CONTAINER_ID)
        self.assertEqual(runtime.agent_network.name, self.agent_network.name)
        self.assertEqual(runtime.uplink_network.name, self.uplink_network.name)
        self.assertEqual(runtime.allowed_hosts, ("api.openai.com",))
        self.assertEqual(runtime.token_path.stat().st_mode & 0o777, 0o400)
        args = self.run_args
        self.assertIsNotNone(args)
        self.assertIn("--network", args)
        self.assertEqual(args[args.index("--network") + 1], self.agent_network.name)
        self.assertEqual(args[args.index("--network-alias") + 1], "narrow-egress-proxy")
        self.assertIn("--read-only", args)
        self.assertEqual(args[args.index("--cap-drop") + 1], "ALL")
        self.assertIn("--container-private-listener", args)
        self.assertFalse(any(value in {"-p", "--publish"} or value.startswith("--publish=") for value in args))
        self.assertIn("api.openai.com", args)
        self.assertTrue(remove_execution_proxy_token_file(self.store, TASK, EXECUTION))
        self.assertFalse(runtime.token_path.exists())

    @unittest.skipIf(os.name == "nt" or os.getuid() == 0, "Requires a non-root POSIX host identity")
    def test_refuses_sidecar_with_published_host_port(self):
        self.port_bindings = {"3128/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3128"}]}
        self._patch_provisioner()
        with patch("narrow.container_egress.cleanup_execution_egress_proxy_containers", return_value=1), patch(
            "narrow.container_egress.cleanup_execution_egress_uplink_networks", return_value=1
        ), patch(
            "narrow.container_egress.cleanup_execution_agent_networks", return_value=1
        ):
            with self.assertRaisesRegex(NarrowError, "no-publish"):
                provision_execution_egress_proxy(
                    self.store, TASK, EXECUTION, IMAGE, ["api.openai.com"]
                )

    @unittest.skipIf(os.name == "nt" or os.getuid() == 0, "Requires a non-root POSIX host identity")
    def test_proxy_token_cleanup_refuses_symbolic_link(self):
        from narrow.container_egress import _execution_root
        root = _execution_root(self.store, TASK, EXECUTION)
        target = root / "target"
        target.write_text("do-not-delete", encoding="ascii")
        token = root / "proxy.token"
        token.symlink_to(target)
        with self.assertRaisesRegex(NarrowError, "unsafe proxy capability"):
            remove_execution_proxy_token_file(self.store, TASK, EXECUTION)
        self.assertTrue(target.exists())


    @unittest.skipIf(os.name == "nt" or os.getuid() == 0, "Requires a non-root POSIX host identity")
    def test_codex_container_agent_wrapper_wires_manifest_lease_proxy_and_supervisor(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as folder:
            state_dir = Path(folder) / ".narrow"
            state_dir.mkdir()
            self.store.state_dir = state_dir
            workspace = Path(folder) / "workspace"
            workspace.mkdir()
            task_home = state_dir / "runtime" / "agent" / TASK / EXECUTION / "home"
            manifest_path = state_dir / "runtime" / "agent" / TASK / EXECUTION / "policy.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text("{}", encoding="utf-8")
            secret_path = state_dir / "runtime" / "agent" / TASK / EXECUTION / "credentials" / ("codex-" + "d" * 32 + ".secret")
            secret_path.parent.mkdir(parents=True, exist_ok=True)
            secret_path.write_bytes(b"secret")
            secret_path.chmod(0o400)
            lease = CredentialLease(
                path=secret_path,
                state_dir=state_dir,
                provider="codex",
                environment_name="OPENAI_API_KEY",
                task_id=TASK,
                execution_id=EXECUTION,
                expires_at=9999999999.0,
                sha256="0" * 64,
            )
            runtime = EgressProxyRuntime(
                container_id=CONTAINER_ID,
                container_name="narrow-egress-proxy-test",
                token="x" * 48,
                token_path=state_dir / "proxy.token",
                agent_network=self.agent_network,
                uplink_network=self.uplink_network,
                image=IMAGE,
                allowed_hosts=("api.openai.com",),
            )
            task = {"id": TASK}
            with patch.object(self.store, "add_event"), patch(
                "narrow.policy_manifest.write_policy_manifest", return_value=SimpleNamespace(path=manifest_path)
            ), patch(
                "narrow.credential_broker.issue_credential_lease", return_value=lease
            ) as issue, patch(
                "narrow.container_egress.provision_execution_egress_proxy", return_value=runtime
            ) as provision, patch(
                "narrow.container_egress.task_workspace", return_value=workspace
            ), patch(
                "narrow.agent_runtime.build_docker_agent_command", return_value=["docker", "run", "restricted"]
            ) as build, patch(
                "narrow.container_supervisor.run_container_agent", return_value=(0, "container-ok")
            ) as run:
                code, output = run_codex_container_agent(
                    self.store,
                    TASK,
                    EXECUTION,
                    ["codex", "exec", "--json", "--cd", str(workspace), "--sandbox", "workspace-write", "--ask-for-approval", "never", "-"],
                    agent_image=IMAGE,
                    proxy_image=IMAGE,
                    allowed_hosts=["api.openai.com"],
                    stdin_text="bounded prompt",
                )
            self.assertEqual((code, output), (0, "container-ok"))
            issue.assert_called_once_with(self.store, "codex", TASK, EXECUTION)
            provision.assert_called_once_with(self.store, TASK, EXECUTION, IMAGE, ["api.openai.com"])
            build.assert_called_once()
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs["stdin_text"], "bounded prompt")
            self.assertEqual(run.call_args.kwargs["credential_lease"], lease)
            self.assertEqual(run.call_args.args[0], ["docker", "run", "restricted"])


if __name__ == "__main__":
    unittest.main()
