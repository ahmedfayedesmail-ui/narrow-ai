# NARROW 1.0.0 — Product Release Notes

Release date: 2026-11-04

## Product release

NARROW 1.0.0 delivers an integrated local-first workflow for AI-assisted software development.

### Included capabilities
- Project discovery and human-reviewed task-contract proposals.
- Native Claude Code and Codex CLI adapters, plus configurable generic command adapters.
- Dedicated Git workspaces, task-scoped execution locks, persistent local event history, handoffs, and provider telemetry.
- Scope monitoring, bounded verification, optional Docker/Podman verification containers, and signed snapshot-bound check results.
- POSIX/local-Docker Claude/Codex agent execution using digest-pinned images, an execution-scoped internal network, egress-proxy routing, task-scoped credentials, container supervision, and recovery.
- Deterministic review reports, diff previews, sensitive-path risk findings, and verification-gated repair.
- Human acceptance evidence, request-changes, reject, and approve-and-finish controls in the local Control Center and CLI.
- Independent finish gate and auditable human decisions.

## Validation matrix

The product CI workflow validates Python 3.11–3.14, macOS, Windows, Docker E2E, and package build. CodeQL provides automated static analysis. Current results are available from the repository's Actions tab.

## Operating profiles

NARROW supports native provider execution and a separate POSIX/local-Docker agent-container profile. Provider-specific permissions, Docker daemon trust, host-kernel trust, and credential visibility are documented in [SECURITY.md](../SECURITY.md) and the [threat model](THREAT_MODEL.md).
