from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from narrow.container_egress import (
    provision_execution_egress_proxy,
    remove_execution_proxy_token_file,
)
from narrow.container_lifecycle import (
    cleanup_execution_agent_networks,
    cleanup_execution_egress_proxy_containers,
    cleanup_execution_egress_uplink_networks,
)
from narrow.core import Store, create_task, init_project

def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


@unittest.skipUnless(
    os.name != "nt"
    and hasattr(os, "getuid")
    and os.getuid() != 0
    and os.environ.get("NARROW_DOCKER_E2E") == "1"
    and os.environ.get("NARROW_AGENT_IMAGE")
    and os.environ.get("NARROW_PROXY_IMAGE"),
    "Requires the opt-in non-root Linux Docker E2E workflow and digest-pinned images",
)
class ContainerEgressDockerE2ETests(unittest.TestCase):
    def test_internal_agent_has_no_default_route_and_only_proxy_reaches_provider(self):
        agent_image = os.environ["NARROW_AGENT_IMAGE"]
        proxy_image = os.environ["NARROW_PROXY_IMAGE"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            git(root, "config", "user.name", "NARROW Docker E2E")
            git(root, "config", "user.email", "narrow-docker-e2e@example.invalid")
            (root / "src").mkdir()
            (root / "src" / "sample.py").write_text("VALUE = 1\\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "initial project")
            init_project(root)
            store = Store(root)
            task = create_task(
                store, "Exercise container egress", "Verify proxy-only network",
                ["src/**"], [], ["Agent has no default route and proxy can reach the provider."],
            )
            execution_id = str(uuid.uuid4())
            runtime = None
            try:
                runtime = provision_execution_egress_proxy(
                    store, task["id"], execution_id, proxy_image, ["api.openai.com"]
                )
                probe = r'''
import socket
import urllib.error
import urllib.request
from pathlib import Path
from narrow.agent_entrypoint import assert_no_default_route
from narrow.egress_proxy import read_token_file
assert_no_default_route()
try:
    socket.create_connection(("1.1.1.1", 443), timeout=2)
except OSError:
    pass
else:
    raise SystemExit("direct external TCP connection unexpectedly succeeded")
token = read_token_file(Path("/run/secrets/proxy-token"))
proxy = f"http://narrow:{token}@narrow-egress-proxy:3128"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy}))
try:
    response = opener.open("https://api.openai.com/", timeout=15)
    response.close()
except urllib.error.HTTPError as exc:
    if exc.code >= 500:
        raise
except Exception as exc:
    raise SystemExit(f"allowlisted provider tunnel failed: {type(exc).__name__}: {exc}")
try:
    opener.open("https://example.com/", timeout=5)
except urllib.error.URLError as exc:
    if "403" not in str(exc):
        raise SystemExit(f"denied destination did not fail at proxy policy: {exc}")
else:
    raise SystemExit("non-allowlisted destination unexpectedly passed the proxy")
print("proxy-only egress smoke passed")
'''
                command = [
                    "docker", "run", "--rm",
                    "--network", runtime.agent_network.name,
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "--read-only",
                    "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    "--mount", f"type=bind,source={runtime.token_path.resolve(strict=True)},target=/run/secrets/proxy-token,readonly",
                    "--entrypoint", "python", agent_image, "-c", probe,
                ]
                result = subprocess.run(
                    command, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, timeout=45, shell=False, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + "\\n" + result.stderr)
                self.assertIn("proxy-only egress smoke passed", result.stdout)
            finally:
                cleanup_execution_egress_proxy_containers("docker", task["id"], execution_id)
                cleanup_execution_egress_uplink_networks("docker", task["id"], execution_id)
                cleanup_execution_agent_networks("docker", task["id"], execution_id)
                remove_execution_proxy_token_file(store, task["id"], execution_id)


if __name__ == "__main__":
    unittest.main()
