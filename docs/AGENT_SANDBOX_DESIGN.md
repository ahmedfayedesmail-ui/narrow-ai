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

## Egress proxy primitive (experimental)

`python -m narrow.egress_proxy` starts a bounded HTTP CONNECT proxy using only
the Python standard library. It accepts TLS tunnels only on port 443, exact
fully-qualified provider hostnames, a per-instance bearer capability token,
and DNS answers consisting entirely of public IP addresses. It connects to the
numeric address selected during policy validation, avoiding a second DNS
lookup between validation and connection. Wildcards, IP-literal destinations,
private/link-local/reserved DNS results, non-CONNECT methods, oversized headers,
and unauthenticated requests fail closed. The listener refuses wildcard and
public-interface bind addresses.

Example (run on a private, execution-isolated container network):

```sh
python -m narrow.egress_proxy \
  --bind 172.30.0.1 --port 3128 \
  --allow-host api.anthropic.com \
  --token-file /run/narrow/proxy-token
```

The token file must be owner-only (mode 0600 on POSIX) and contain at least
32 random ASCII characters. Clients may use HTTP proxy Basic authentication
(username `narrow`, password equal to the token) or a Bearer proxy header.
The agent receives only this proxy capability, not a provider API key. The
proxy does not decrypt provider TLS; provider TLS
verification remains the responsibility of the client. A provider hostname
allowlist does not prevent the agent from sending task data to that provider.

The proxy also binds the TLS ClientHello SNI to the exact CONNECT hostname. It explicitly rejects the TLS Encrypted ClientHello (ECH) extension: matching only an outer SNI would not prove the encrypted inner hostname. Clients without a visible DNS SNI fail closed.

**This is a proxy primitive, not end-to-end egress enforcement.** Until the
agent launcher creates a per-execution network with no direct internet route,
attaches the agent only to that network, and routes the proxy's upstream
traffic through a separate controlled uplink, an agent can bypass a standalone
proxy. This module is not wired into agent execution yet and must not be treated
as an enabled security control.

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

## Provider credential lease primitive (pre-launch integration)

The preparatory `narrow.credential_broker` module can copy one explicitly
selected `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from the current host process
environment into a short-lived, task/execution-specific file under the private
NARROW runtime directory. It does not inspect Claude/Codex global login
profiles, enumerate other environment variables, or persist the key in the
task journal. On POSIX, the directory is owner-only (0700), the file is
owner-read-only (0400), and a lease validation checks its expiry, file type,
link count, size, and SHA-256 digest. Revocation deletes only that exact file.
The implementation currently fails closed on Windows until native restricted
DACL enforcement is available.

This is **not** a way to hide a provider key from the agent. If the future
launcher mounts the credential file into the agent container, the provider CLI,
agent, and tools running with that container identity can read it. The lease
reduces exposure of the host's global provider profile and unrelated secrets;
it does not protect a key from a malicious model/tool. Prefer provider-issued
short-lived, narrowly scoped credentials. The TTL is enforced when the
launcher validates the lease; the launcher must revoke it in a `finally`
path, and crash-recovery cleanup must remove expired lease files. This helper
is not yet wired into an agent launcher or an automated revocation supervisor.

A minimal in-container `narrow.agent_entrypoint` now consumes a single
read-only credential mount. It accepts only the exact `claude` or `codex`
executable name (no arbitrary executable path), removes both provider API-key
variables inherited from its parent, and forwards only a small runtime
environment allowlist plus the one selected provider key. The credential file
must be a regular, unlinked, bounded file with POSIX mode 0400. This entrypoint
is a building block; the host launcher still needs to mount the manifest,
credential, worktree, and task-scoped home, invoke this entrypoint, supervise
the container process, and import hook telemetry after exit.

A Docker CLI process being assigned to a Windows Job Object does not prove
that a daemon-managed container will be terminated on host crash. Container
lifecycle needs a separately supervised container ID, explicit cleanup, and
crash-recovery reconciliation.

The preparatory `narrow.container_lifecycle` module now supplies execution-
scoped ownership labels and ownership-checked list/remove operations for agent
containers. Cleanup re-inspects the container immediately before removal and
refuses to act unless all NARROW managed/component/task/execution/schema labels
match exactly. This prevents an ordinary cleanup path from deleting an
unrelated container by a reused name or stale ID. Docker/Podman daemon access
remains a trusted authority and can forge labels; these labels are an ownership
guard, not a security boundary against a daemon administrator. Durable
execution leases, host-crash reconciliation, network cleanup, and integration
with an actual agent launcher remain outstanding.

The same module can now create a per-execution Docker/Podman bridge network
with `--internal` and exact NARROW task/execution labels. It verifies the
engine's inspected result is an internal bridge before returning it. Cleanup
will refuse to remove that network while any matching NARROW agent container
still exists, and it re-inspects ownership before removal. This is only the
isolated network primitive: a launcher still needs to attach the agent and a
separately controlled egress proxy to the correct network topology, block
direct routes, persist execution state, and reconcile after host crashes.

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


## First container launch-planning slice

The preparatory `narrow.agent_runtime` module now requires immutable digest-
pinned image references and rebuilds only the known Codex adapter arguments.
It translates the host worktree argument to the virtual `/workspace` path,
preserves the configured workspace-write sandbox and non-interactive approval
policy, allows only the reviewed model/reasoning-effort options, and requires
the stdin prompt sentinel. It refuses Claude until its host-generated settings
and hook files have a container-specific replacement.

This is argument planning only. It does not start Docker, create the proxy
sidecar, supervise a container ID, import hook events, revoke credentials, or
recover after a host crash. The normal `narrow drive` workflow does not use
this module yet; native host execution remains unchanged. Do not treat this
slice as a working agent sandbox.


## Host-side Docker process supervision slice

A preparatory `narrow.container_supervisor` module now supervises one
Codex-container process on POSIX hosts. It requires the local default Docker
context, an exact task/execution-labeled internal network, the matching active
NARROW task store and short-lived Codex credential lease, and the exact
task-scoped workspace, task home, policy, and credential bind-mount sources.
It rejects unapproved Docker flags and mount targets, rejects host environment
pass-through and alternate providers, and requires a digest-pinned image,
read-only root filesystem, dropped capabilities, no-new-privileges, bounded
tmpfs, a non-root container user, and the authenticated egress-proxy settings.

The supervisor streams bounded JSONL events to the host callback, retains
non-event diagnostics within a configured limit, uses a private cidfile, and
attempts exact-label container cleanup after completion, timeout, callback
failure, or interruption. It is POSIX/local-Docker only. It does not yet launch
or supervise the egress proxy, import the container event spool, clean the
per-execution network, reconcile after an abrupt host crash, or connect to the
normal `narrow drive` loop. The caller must provision the task-scoped home
directory before invoking it. This is a supervision primitive, not a complete
agent runtime or an enabled end-to-end sandbox.


## Execution-finalization safeguards

The POSIX Docker supervisor now imports the canonical task-home hook spool
(`.local/state/narrow/hook-events.jsonl`) after container cleanup. Imported
records are tagged as untrusted agent telemetry and remain diagnostic only.
The supervisor then revokes the exact task/execution credential lease and
removes the execution's internal network only after confirming no matching
agent container remains. Cleanup failures are surfaced to the caller rather
than silently reported as a successful run.

Credential revocation is wrapped around the supervisor preflight as well, so
an invalid image/command, missing network, or other pre-launch validation
failure also revokes the selected lease. The runtime spool path now matches
the host importer's canonical path.

This still does not provide abrupt-host-crash recovery: a SIGKILL or host
failure can bypass Python finalizers. Durable recovery reconciliation and a
separately managed egress proxy are still required before enabling the agent
runtime through the standard drive workflow.
