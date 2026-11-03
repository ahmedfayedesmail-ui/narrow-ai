# Optional containerized verification

The development branch supports Docker and Podman wrappers for configured
verification commands. This boundary is opt-in so existing projects do not
silently change behavior when upgrading.

## Build a local image

The Python/pytest starter is examples/containers/python-checks.Dockerfile.
Build it on the machine where NARROW runs:

docker build -f examples/containers/python-checks.Dockerfile -t narrow-checks:local .

For a real project, add the exact reviewed test dependencies and required
system libraries to your own image. The container runner does not install
packages and never pulls an image during verification.

## Enable in .narrow/config.json

Add this object at the top level:

{
  "verification_sandbox": {
    "mode": "auto",
    "image": "narrow-checks:local",
    "memory": "2g",
    "cpus": "2",
    "pids_limit": 256,
    "workspace_read_only": true
  }
}

Mode can be off, auto, docker, or podman. Auto prefers Docker and falls back
to Podman. The selected engine and image must already be installed locally.
If the engine or image is unavailable, NARROW fails closed before executing
the verification command.

## What is enforced

- Container networking is disabled with network=none.
- The container root filesystem is read-only.
- All Linux capabilities are dropped and no-new-privileges is enabled.
- PID count, memory, and CPU are limited.
- A small no-exec /tmp tmpfs is provided.
- Only the task's isolated Git workspace is bind-mounted into the container.
- No host environment variables, home directory, provider credentials, or
  project-parent directory are passed into the container.
- The container is removed automatically; NARROW attempts forced cleanup if
  the command times out or is interrupted.

The workspace mount is read-only by default. This keeps a verification command
from changing code or test files while it runs. The container receives a
writable /tmp and HOME=/tmp for temporary data. If a specific build/test command
must write into the task workspace, set workspace_read_only to false only after
reviewing that command and its expected generated files.

## Important limits

This is a stronger boundary for verification checks, not a universal sandbox
around the AI agent. The agent still uses its provider-specific execution
boundary. The Docker/Podman daemon and host kernel remain trusted components;
a user with control of the daemon may have substantial host privileges.
Container escape vulnerabilities, kernel compromise, malicious images, and
resource exhaustion outside the configured cgroup limits remain possible.

A container image can contain additional network clients, but the container
network namespace is disconnected during checks. If tests require network
access, this mode will fail; do not casually enable network access for untrusted
tests. Build images from reviewed dependencies and pin base images/digests
according to your organization's supply-chain policy.

This feature does not provide Firecracker or another microVM runtime, does not
contain generic AI agent execution, and is not an independent security review.
For high-risk repositories use a disposable VM with no host credentials,
restricted mounts, and a low-privilege account.
