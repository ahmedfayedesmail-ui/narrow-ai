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
    "workspace_read_only": true,
    "dependency_caches": ["pip", "npm"]
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


## Task-scoped dependency caches

The optional `dependency_caches` list supports `pip`, `npm`, `pnpm`, and
`yarn`. NARROW creates a separate private cache directory per task under its
user cache (not under the repository or home-directory package-manager config)
and mounts only the selected cache directories into the container. It passes
the corresponding package-manager cache environment variable. No host
`~/.npm`, `~/.cache/pip`, `~/.config`, or credential directory is mounted.

Cache mounts are writable, so treat their contents as untrusted artifacts.
They are per-task to avoid sharing a potentially poisoned cache across tasks.
The configuration defaults to an empty list; enabling caches is explicit.
The cache directories may be removed manually when the task is no longer needed.

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

## Other hardening included on this feature branch

The local Control Center remains read-only. Each dashboard launch generates a
cryptographically random API token. The token is passed only in the URL
fragment (not sent in HTTP requests or Referer headers), moved into browser
session storage, and sent in the `X-NARROW-Token` header. API requests also
retain loopback Host and same-origin checks. POST/PUT/DELETE remain disabled
even with a valid token; this is not a write-mode dashboard.

On Windows 10 and Windows Server 2016 or newer, the supervised agent and
verification children are created with `CreateProcessW` and
`STARTUPINFOEXW`. NARROW creates a non-inheritable Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and supplies its handle through
`PROC_THREAD_ATTRIBUTE_JOB_LIST` in the same process-creation call. The
child is therefore born inside the Job; there is no later
`AssignProcessToJobObject` window and no suspended orphan to clean up. The
implementation also supplies a restricted standard-handle list because the
standard-library `Popen` helper does not expose the Job List attribute. Its
Win32 declarations use pointer-sized handle and attribute types.

The launcher deliberately does not use `CREATE_BREAKAWAY_FROM_JOB` and does
not enable `BREAKAWAY_OK` or `SILENT_BREAKAWAY_OK`. Ordinary descendants
created through `CreateProcess` therefore remain in the Job. Unsupported
Windows versions, incompatible parent Job constraints, or any attribute/API
failure cause launch to fail closed; there is no uncontained fallback.
The Job handle is non-inheritable and remains owned by the NARROW supervisor,
so forced supervisor termination closes it and activates kill-on-close.
Windows Job assignment does not cover broker-created processes that are not
ordinary descendants, nor does it itself terminate daemon-managed Docker
containers. The hard-kill and deep-descendant tests are added but still require
a real Windows run before this behavior can be described as validated.

Verification rows now carry an HMAC-SHA256 signature over the task, check name,
snapshot, command, result, duration, timestamp, and digest of the retained
output. NARROW requires a valid signature for the latest configured check
before `finish`. The 32-byte key is stored in a private per-project file under
the user's NARROW cache, outside the task workspace. This detects tampering
with SQLite rows/events alone; it is not protection against a compromised
host account or an attacker who can read or replace the local key.

For automatic contract generation, NARROW records the generated proposal and
the human-approved contract as separate immutable versions and stores a
field-level diff. Use `narrow contract-history --task <id>` to inspect it.
The approved contract remains immutable during a task; changing scope requires
a new task rather than silently mutating an active task's boundaries.
