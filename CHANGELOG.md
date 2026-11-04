# Changelog

## 1.0.0 — 2026-11-04

NARROW 1.0.0 brings local-first AI coding workflow supervision into one cohesive product: task contracts, agent execution, verification, evidence review, human decisions, and the local Control Center.

### Product capabilities
- Project discovery and conservative setup profiles with one-command setup.
- Native Claude Code and Codex CLI adapters, plus configurable command adapters.
- Dedicated task workspaces, persistent local journal, session handoffs, scope monitoring, and single-flight task execution.
- Optional Docker/Podman verification and the POSIX/local-Docker Claude/Codex agent runtime with digest-pinned images, execution-scoped networks, egress proxy routing, task-scoped credentials, supervision, and recovery.
- Signed, snapshot-bound verification results and bounded repair continuation governed by deterministic review evidence.
- Review queue, unified diff preview, deterministic risk findings, evidence capture for each acceptance criterion, request-changes, reject, and approve-and-finish controls.
- Independent finish gate and auditable human decision events.
- Local Control Center and JSON-capable CLI workflows.

### Validation matrix
The product CI workflow exercises Python 3.11–3.14, macOS, Windows, Docker E2E, package build, and CodeQL.

## Previous releases

### 0.5.1
- Refreshed the source-distribution README and package metadata.
- Added public project, documentation, issues, and changelog URLs.
- Added OIDC-based package publishing with TestPyPI-first verification.

### 0.5.0
- Added per-task single-flight execution leases across agent, verification, and finish operations.
- Passed the lease handle to first-level agent and verification processes.
- Added cross-process tests for contention, inherited-descriptor lifetime, path traversal, symlinked lock directories, and hard-linked lock files.
- Published the repository source tree alongside versioned source archives.

### 0.4.0
- Added the responsive local Control Center, task portfolio, and sanitized activity timeline.
- Added Claude Code and Codex reasoning-effort controls.
- Expanded Claude telemetry with task and subagent lifecycle events.
- Added provider compatibility diagnostics.

### 0.3.0
- Strengthened Claude shell sandbox configuration and fail-closed hook behavior.
- Added shell read and network restrictions for the supported native profile.
- Added provider-specific tool controls and Codex non-interactive execution.

### 0.2.0
- Added native Claude Code and Codex CLI adapters.
- Added structured JSONL event parsing, sanitized provider telemetry, and local provider session continuity.
- Added Claude lifecycle hooks, provider detection, diagnostics, and Work Receipt export.

### 0.1.0
- Added local task contracts and the SQLite event journal.
- Added a separate Git copy per task with remotes removed.
- Added explicit path allowlists and denylists, generic command execution, handoffs, verification, and bounded repair.
- Added snapshot-bound checks and the human acceptance gate.
