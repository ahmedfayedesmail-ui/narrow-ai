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
            (root / "src" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
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
import base64
import socket
import ssl
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
authorization = base64.b64encode(f"narrow:{token}".encode("ascii")).decode("ascii")
def connect_tunnel(host):
    sock = socket.create_connection(("narrow-egress-proxy", 3128), timeout=10)
    request = (
        f"CONNECT {host}:443 HTTP/1.1\\r\\n"
        f"Host: {host}:443\\r\\n"
        f"Proxy-Authorization: Basic {authorization}\\r\\n"
        "Proxy-Connection: keep-alive\\r\\n\\r\\n"
    ).encode("ascii")
    sock.sendall(request)
    response = bytearray()
    while b"\\r\\n\\r\\n" not in response and len(response) <= 16 * 1024:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response.extend(chunk)
    if len(response) > 16 * 1024 or b"\\r\\n\\r\\n" not in response:
        sock.close()
        raise RuntimeError("proxy returned an incomplete CONNECT response")
    status = bytes(response).split(b"\\r\\n", 1)[0].decode("ascii", "replace")
    return sock, status
tunnel, status = connect_tunnel("api.openai.com")
if " 200 " not in status:
    tunnel.close()
    raise SystemExit(f"allowlisted provider CONNECT failed: {status}")
tls = ssl.create_default_context().wrap_socket(tunnel, server_hostname="api.openai.com")
tls.sendall(b"GET / HTTP/1.1\\r\\nHost: api.openai.com\\r\\nConnection: close\\r\\n\\r\\n")
response = bytearray()
while b"\\r\\n\\r\\n" not in response and len(response) <= 16 * 1024:
    chunk = tls.recv(4096)
    if not chunk:
        break
    response.extend(chunk)
tls.close()
if not response:
    raise SystemExit("allowlisted provider returned no HTTPS response")
try:
    provider_status = int(bytes(response).split(b"\\r\\n", 1)[0].split()[1])
except (ValueError, IndexError):
    raise SystemExit("allowlisted provider returned a malformed HTTPS status line")
if provider_status >= 500:
    raise SystemExit(f"allowlisted provider returned HTTP {provider_status}")
denied, denied_status = connect_tunnel("example.com")
denied.close()
if " 403 " not in denied_status:
    raise SystemExit(f"non-allowlisted destination was not denied by proxy policy: {denied_status}")
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
                self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
                self.assertIn("proxy-only egress smoke passed", result.stdout)
            finally:
                cleanup_execution_egress_proxy_containers("docker", task["id"], execution_id)
                cleanup_execution_egress_uplink_networks("docker", task["id"], execution_id)
                cleanup_execution_agent_networks("docker", task["id"], execution_id)
                remove_execution_proxy_token_file(store, task["id"], execution_id)


if __name__ == "__main__":
    unittest.main()
