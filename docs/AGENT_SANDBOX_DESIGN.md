# NARROW — Agent Runtime Architecture

This document describes the provider and container execution profiles used by NARROW 1.0.0.

## Native provider execution

The native Claude Code adapter configures its provider shell sandbox, selected direct-file hooks, task-specific settings, and structured event parsing. The native Codex adapter uses non-interactive execution with the provider's `workspace-write` policy. Generic command adapters use the configured argv and the invoking user's operating-system permissions unless the user supplies an independent boundary.

NARROW keeps task contracts, its SQLite event journal, the HMAC key, and review evidence on the host. Provider authentication remains with the selected provider CLI and account.

## Container agent profile

The optional POSIX/local-Docker profile supports native Claude Code and Codex CLI. It uses a shared digest-pinned agent image containing both CLIs and a separately digest-pinned egress-proxy image.

The host supervisor creates an execution-scoped internal agent network and a separate uplink network for the proxy. The agent is attached only to the internal network. The proxy sidecar is attached to both networks and routes allowed provider traffic. The supervisor inspects image references, ownership labels, mount sources, network attachments, route state, listener readiness, resource bounds, and container security settings before launching the agent.

The agent image runs non-root with a read-only root filesystem, dropped Linux capabilities, no-new-privileges, bounded CPU/memory/PIDs, and limited writable temporary storage. The task workspace is mounted at a fixed container path. Provider settings, the policy manifest, the proxy capability, and the selected credential are mounted only at task/execution-specific paths.

### Claude profile

Claude receives a container-specific settings file. Its hooks invoke the in-image NARROW CLI and use the read-only policy manifest for selected direct file operations. MCP is configured as empty for this profile, and provider-managed Task/subagent tools are disabled. Claude's normal host shell-sandbox setting is removed inside this profile because the outer Docker boundary supplies the process, filesystem, and network container.

### Codex profile

Codex runs through the native `codex exec` adapter with JSONL output, the virtual `/workspace` directory, `workspace-write`, and non-interactive approval policy. Host-only worktree and policy paths are translated to fixed container paths.

## Credentials and network

The host credential broker accepts only the selected provider's explicit API-key variable and creates a short-lived task/execution-specific credential file. The container receives only that provider's key; it does not mount the developer's global provider home or login profile.

The provider process and tools running inside the container can read the mounted key. Treat that key as available to the agent during execution and prefer short-lived credentials with limited scope.

The egress proxy accepts the configured provider hostnames and TLS CONNECT traffic under a per-execution capability. DNS validation rejects non-public destinations, and the proxy connects to the validated numeric address. The agent container has no direct default route; only the proxy sidecar has an uplink.

## Lifecycle and recovery

The supervisor streams bounded structured events to the host, treats imported hook records as untrusted telemetry, and performs ordered cleanup. It removes the agent container before the proxy sidecar and its uplink/internal networks, revokes the exact credential lease, and records cleanup failures. The `narrow recover-agent` command reconciles a specifically identified stale execution after an interruption.

Container ownership labels and reinspection checks prevent routine cleanup from targeting unrelated containers. Docker daemon administrators can still control the daemon and forge container metadata; the daemon and host kernel remain trusted.

## Verification containers

Verification containerization is configured separately from the agent runtime. It can run configured checks with networking disabled, a read-only container root, reduced capabilities, no-new-privileges, and CPU/memory/PID limits. The selected workspace mount and dependency caches are governed by the local verification configuration.

## Operational boundaries

- The Docker agent profile is supported on POSIX hosts using the local Docker context.
- The Docker daemon, host kernel, and host services reachable through the Docker bridge remain trusted infrastructure.
- A provider key mounted into a container is readable by code executing in that container.
- Native provider enforcement is provider-specific; generic command adapters and direct low-level execution use the invoking user's permissions unless independently constrained.
- Provider hooks cover the documented hook surface and do not replace operating-system access controls.
- Verification programs are user-selected executable code and should be reviewed before execution.

See [SECURITY.md](../SECURITY.md) and [the threat model](THREAT_MODEL.md) for the full execution-profile guarantee matrix.
