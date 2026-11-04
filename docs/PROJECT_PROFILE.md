# NARROW — Product Profile

**Product:** NARROW 1.0.0  
**Category:** Local-first AI coding workflow supervisor  
**Tagline:** AI can start the work. NARROW keeps the workflow on track, verifies the result, and leaves the final decision in human hands.

## Product overview

NARROW coordinates AI-assisted software work from task definition through implementation, verification, review, and completion. It provides a structured task contract, a dedicated Git workspace, persistent local continuity, provider-aware execution, live scope monitoring, bounded verification and repair, deterministic evidence review, and an interactive local Control Center.

NARROW works with the developer's existing Claude Code, Codex CLI, or configured command adapter. It keeps workflow records local while making each stage observable and reviewable.

## Core value

- **Controlled execution:** every task begins with an explicit objective, allowed paths, denied paths, acceptance criteria, and execution budgets.
- **Continuity:** task notes, decisions, handoffs, events, provider metadata, and verification records remain available across sessions.
- **Change visibility:** dedicated Git workspaces and scope monitoring make an agent's changes easier to inspect.
- **Evidence-driven verification:** configured checks are recorded against workspace snapshots, and stale evidence is not treated as current.
- **Bounded autonomy:** repair attempts are limited and gated by deterministic review findings.
- **Human authority:** acceptance criteria require evidence, and the final completion decision remains an explicit human action.
- **Local-first operation:** NARROW does not require a NARROW cloud account or platform API key.

## Main capabilities

### Task and workspace management
Task contracts, separate Git workspaces, allowed/denied path policies, task locks, local SQLite event journal, task portfolio, handoff generation, and contract history.

### Agent orchestration
Native Claude Code and Codex adapters, structured provider event parsing, optional provider subagents where supported, generic command adapters, execution budgets, handoff/resume, and provider effort controls.

### Verification and repair
Reviewed argv-based checks, optional Docker/Podman verification containers, signed check records, snapshot freshness checks, bounded repair attempts, and verification-gated continuation.

### Review and completion
Deterministic review reports, unified diff previews, scope and integrity findings, sensitive-path risk flags, human evidence for acceptance criteria, request-changes and reject decisions, and approval through the independent finish gate.

### Control Center
Local dashboard with a task queue, task detail, review results, unified diff preview, acceptance evidence entry, decision actions, and sanitized activity history.

### Security profiles
Provider-specific controls, task-scoped runtime credentials for the Docker agent profile, digest-pinned images, execution-scoped networks, egress proxy routing, container ownership checks, ordered cleanup, and explicit recovery tools.

## Supported operating modes

| Mode | Role |
|---|---|
| Native Claude Code | Provider-managed shell controls plus selected NARROW direct-file hooks |
| Native Codex CLI | Provider-managed workspace-write execution |
| Generic command adapter | Configured argv command with NARROW task orchestration and monitoring |
| Docker agent profile | POSIX/local-Docker execution for native Claude and Codex with an internal agent network and egress proxy |
| Verification container | Optional isolated execution for configured verification commands |

## Intended users

- Individual software developers using AI coding agents.
- Engineering teams that need repeatable task scope and local evidence.
- Maintainers coordinating multi-session implementation work.
- Developers who want a clear review and acceptance record before task completion.

## Operating model

1. Discover the project and review the proposed setup.
2. Define and approve the task contract.
3. Execute the task in its dedicated workspace.
4. Monitor changes and preserve continuity.
5. Run current verification and bounded repair.
6. Review the diff and deterministic evidence.
7. Record human acceptance and finish through the independent completion gate.

## Privacy and security

Task state and evidence are stored locally. Provider authentication remains with the provider CLI and account selected by the developer. NARROW exposes the security posture through `narrow security` and documents each execution boundary in [SECURITY.md](../SECURITY.md) and the [threat model](THREAT_MODEL.md).

## Product documentation

- [Product architecture](PRODUCT_ARCHITECTURE.md)
- [Security policy](../SECURITY.md)
- [Threat model](THREAT_MODEL.md)
- [Agent runtime architecture](AGENT_SANDBOX_DESIGN.md)
- [Package publishing](PUBLISHING.md)
