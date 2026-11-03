# Agent runtime sandbox — implementation gate

This document defines the remaining boundary that must be built before NARROW
can claim that the AI agent itself runs inside an OS container. The existing
Docker/Podman implementation wraps verification commands only.

## Required runtime shape

Use a separate, versioned agent image for each supported provider/runtime
combination. The image contains the pinned provider CLI, the compatible NARROW
runtime/hooks if required, and only project toolchains that have been reviewed.
It must run as a non-root user with a read-only image filesystem, small writable
tmpfs mounts, no privileged mode, all Linux capabilities dropped,
no-new-privileges, bounded CPU/memory/PIDs, and an explicit wall-clock limit.

At runtime, mount only:
- The task's isolated worktree at /workspace as read/write.
- A task-scoped provider-session directory at a fixed path, without mounting
  the developer's full home directory.
- A task-scoped dependency cache only when explicitly enabled.

Never mount the original checkout, repository parent, SSH/cloud config, Docker
or Podman sockets, home directory, host root, or broad user cache directories.
The NARROW SQLite database and HMAC key must remain on the host. Provider events
and terminal output should flow to the host supervisor over bounded stdout/JSONL;
the container must not write directly to the host journal.

## Credentials and network

A provider CLI must be authenticated without copying its global host profile.
The runtime may pass only an explicit, provider-specific credential variable
such as ANTHROPIC_API_KEY or OPENAI_API_KEY when the user has configured it.
The agent process can read the key while it runs; environment variables do not
hide a credential from the model or tools executing in that container. Prefer
short-lived, narrowly scoped credentials and support revocation. Do not pass
arbitrary host environment variables.

The container needs outbound access to the model provider. A plain Docker bridge
is not an egress policy. Production mode must route requests through an
allowlisting egress proxy or equivalent host firewall policy that permits only
the required provider endpoints and explicitly reviewed registries. Do not
claim network isolation while the agent can reach arbitrary internet hosts.

## Provider integration constraints

The built-in Claude adapter currently creates task-specific settings and hook
files under the host-side .narrow/providers directory. Those files cannot be
mounted wholesale into an agent container: doing so would expose host-side
journal state or provider credentials.

The container implementation must instead:
1. Generate a container-specific settings profile with only the selected
   provider's required hooks/tools.
2. Store resumable sessions in a task-scoped host directory, not the global
   provider home.
3. Translate all host worktree/settings/prompt paths to fixed container paths.
4. Keep the structured event parser and journal on the host.
5. Fail closed if the image lacks the selected CLI, a required credential is
   missing, or the provider profile cannot be represented safely.
6. Ensure subagents, shell tools, and provider helper processes remain inside
   the same container boundary.

A Docker CLI process being assigned to a Windows Job Object does not prove
that a daemon-managed container will be terminated on host crash. Container
lifecycle needs a separately supervised container ID, explicit cleanup, and
crash-recovery reconciliation.

## Required acceptance tests

- The agent can modify only the isolated task copy; a sentinel in the original
  checkout remains byte-for-byte unchanged.
- Reads of host home, SSH/cloud credentials, parent repository paths, Docker
  sockets, and unrelated project directories fail from inside the container.
- The provider can authenticate using only the configured temporary credential
  path and can resume a task session after the container is recreated.
- Direct file tools, shell commands, child agents, and helper processes all
  use the same task boundary.
- Arbitrary outbound network destinations are blocked; only the declared
  provider egress policy works.
- Killing the NARROW host, the Docker CLI, or the container produces a
  recoverable task state and no continuing agent process/container.
- Linux, Windows/WSL2, and macOS behavior is tested separately. Docker Desktop,
  rootless Docker, and Podman must not be assumed to have identical isolation.

Until these tests pass, the existing provider-specific sandbox/hook protections
remain in place and the feature must be described as verification-only
containerization, not a universal agent sandbox.
