## 0.5.1 — 2026-11-03 (Alpha)

### Changed
- Refresh the source-distribution README so package-index pages no longer claim that no tagged GitHub release or package publication exists.
- Include the public project, documentation, issues, and changelog URLs in distribution metadata.
- Add a least-privilege, OIDC-based publishing workflow with TestPyPI-first verification.

### Notes
- This is a packaging/documentation release only; application runtime behavior is unchanged.
- Authenticated Claude/Codex end-to-end validation, Windows crash-lifetime adversarial testing, and independent security review remain open.

## Post-0.5.1 readiness hardening (unreleased)

### Added
- A reproducible local end-to-end demo for a bounded fail-then-repair/handoff flow and a bounded failure flow. It uses a deterministic fake agent and leaves final acceptance to a human.
- A dedicated human-review procedure covering tracked diffs, untracked files, snapshot-bound verification, criterion evidence, and the final finish gate.
- A v0.5.0 threat model that distinguishes native Claude, native Codex, generic adapters, direct command wrappers, verification programs, and the local dashboard.

### Fixed
- The Control Center's Ready for Review counter and status badge now use the actual persisted `READY_FOR_REVIEW` state. The previous dashboard counter compared against a different event-like label and could show zero when a task was ready for a human.

### Validation work
- CI now includes macOS full-suite and demo jobs, Windows demo workflows, and clean wheel/source-distribution build and wheel-install checks. Results are recorded in the QA document after the workflow completes.
- Documentation now explicitly states that generic adapters/direct `run`/`resume` commands do not gain an OS sandbox from NARROW and that verification commands retain the invoking user's permissions.

## 0.5.0 — 2026-11-03 (Alpha)

### Added
- Per-task single-flight execution leases. Concurrent drive loops, direct agent runs, verification commands, and finish checks for the same task are rejected instead of racing over one workspace.
- The lease handle is passed to first-level agent and verification processes. On POSIX, the inherited file descriptor keeps the lease held if the NARROW supervisor exits while that child remains alive.
- Cross-process regression tests for contention, independent task IDs, inherited-descriptor lifetime, path traversal, symlinked lock directories, and hard-linked lock files.
- The full source tree is published at repository root for direct inspection and contribution, alongside the versioned release archive.

### Safety and limitations
- The lease is local process coordination, not an operating-system sandbox.
- Detached descendants that deliberately close inherited descriptors may outlive the first-level lease. After a supervisor crash, inspect running processes and the isolated workspace before restarting.
- The Windows inheritable-handle path is implemented but still needs dedicated crash-lifetime validation with Windows CI and real provider runs.
- No automatic commit, merge, push, deploy, or human acceptance was added.

# Changelog

All notable changes to NARROW are documented here.

## 0.4.0 — 2026-11-03 (Alpha)

### Added
- Local loopback-only, read-only Control Center with responsive UI, task overview, scope summary, current/stale verification status, and sanitized activity timeline.
- `narrow tasks` with active/all task listing and sanitized JSON output.
- Cross-provider reasoning-effort controls: Claude `--effort` and Codex `model_reasoning_effort` config override. Effort remains model/account dependent and defaults to the provider setting when omitted.
- Claude TaskCreated/TaskCompleted, SubagentStart/SubagentStop, and PostToolUseFailure hook telemetry with strict metadata minimization.
- Explicit Claude Agent/task tool opt-in through `--allow-subagents`; it remains disabled by default.
- Separate Claude Code `--ultracode` opt-in for dynamic multi-agent workflows, guarded behind `--allow-subagents` and disallowed with another explicit effort value.
- Provider version compatibility metadata for Claude's newer task/subagent telemetry hooks.

### Changed
- Package version advanced to 0.4.0.
- Control Center APIs expose sanitized summaries only; note content, prompts, tool arguments/results, and raw check output are not returned.
- Documentation now describes the real security boundary: the isolated Git copy is not an OS sandbox and user-level permissions remain in effect.

### Safety notes
- The dashboard binds to loopback, accepts read-only requests, and applies restrictive browser security headers.
- Subagents share the same task workspace and user-level execution permissions; enabling them is an explicit choice, not a separate containment boundary.
- No automatic commit, merge, push, deployment, or completion acceptance is introduced.
- Independent security review and authenticated provider E2E remain open release gates.

## 0.3.0 — 2026-11-03 (Alpha)

### Added
- Claude Code strict shell sandbox is required; fail closed when unavailable and prohibit unsandboxed retries.
- Deny shell reads outside the task working directory and common credential files/secrets to sandboxed subprocesses.
- Deny shell network access by default; allow only explicit, narrow domain entries from local config.
- Disable MCP for automated Claude sessions and narrow the built-in tool set.
- Fail-closed path gates for Claude direct Read/Glob/Grep and Write/Edit-family tools.
- Move native task prompts to stdin and keep prompt content out of process arguments and the NARROW journal.
- Codex non-interactive exec runs with `workspace-write` and `--ask-for-approval never`.
- Bounded drive/verify/repair flow, snapshot-bound checks, and human acceptance gate.

### Safety notes
- Claude's direct-file hook is not a sandbox and does not cover shell/MCP side effects.
- Codex uses its workspace-write sandbox and non-interactive approval-deny mode, but NARROW does not install a Codex-native pre-tool file-path hook.
- Isolated Git copies still run agent and verification processes with the current user's OS permissions.

## 0.2.0 — 2026-11-03 (Alpha)

### Added
- Native Claude Code and Codex CLI adapters for the bounded `drive` workflow.
- Structured JSONL event parsing, sanitized provider telemetry, and local provider session/thread ID continuity.
- Claude lifecycle hooks, provider detection, a read-only doctor command, and Work Receipt export.

### Changed
- Agent prompts, assistant content, tool inputs, and tool results are not stored in NARROW's event journal.
- Structured provider output is drained without placing task prompts in process arguments.

## 0.1.0 — 2026-11-03 (Alpha)

### Added
- Local task contract and SQLite event journal.
- Separate full Git copy per task, with remotes removed.
- Explicit path allowlist and denylist.
- Generic CLI agent wrapper, compact handoff, explicit check approval, and bounded repair loop.
- Snapshot-bound checks and human acceptance gate.
- Initial security and threat model.

## Post-v0.5.1 container runtime groundwork (unreleased)

### Added
- Task/execution-scoped JSON policy manifests with virtual `/workspace` paths and explicit integrity validation.
- POSIX provider-credential leases, a constrained in-container Codex entrypoint, and digest-pinned Codex image/argument validation.
- A deterministic Docker agent command builder with explicit mounts, non-root identity, read-only root filesystem, bounded resources, no-new-privileges, and per-execution proxy capability settings.
- A POSIX/local-Docker host supervisor with bounded JSONL event streaming, exact ownership labels, credential revocation, cleanup, and untrusted hook-telemetry import.
- An operator-invoked `narrow recover-agent` command for one explicitly selected stale task/execution.
- Ownership-checked lifecycle primitives for execution-specific internal agent networks, egress-proxy sidecars, and a separate proxy uplink network.

### Security and maturity limits
- The egress proxy itself remains an experimental CONNECT primitive. Sidecar creation, network attachment, readiness verification, and default-route/bypass enforcement are not yet implemented.
- The Docker agent command builder and supervisor are not integrated into the standard `narrow drive` workflow. Native provider execution remains unchanged.
- The container runtime is not a supported end-to-end sandbox. Do not claim arbitrary-egress blocking, host-crash containment, Windows support, or production readiness.
- This work is pre-release engineering groundwork; it does not change the published v0.5.1 package or its Alpha maturity classification.
