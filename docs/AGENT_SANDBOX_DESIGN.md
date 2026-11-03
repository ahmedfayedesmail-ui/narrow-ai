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
