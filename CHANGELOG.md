## 1.0.0 — 2026-11-04

NARROW 1.0.0 brings the local-first AI coding workflow into one cohesive product experience: task contracts, agent execution, verification, evidence review, human decision controls, and the local Control Center.

### Product capabilities
- Task discovery and conservative project profiles with one-command setup.
- Native Claude Code and Codex CLI adapters, plus configurable command adapters.
- Dedicated task workspaces, persistent local journal, session handoffs, scope monitoring, and single-flight task execution.
- Optional Docker/Podman verification and the POSIX/local-Docker Claude/Codex agent runtime with digest-pinned images, execution-scoped networks, egress proxy routing, task-scoped credentials, supervision, and recovery.
- Signed, snapshot-bound verification results and bounded repair continuation governed by deterministic review evidence.
- Review queue, unified diff preview, deterministic risk findings, evidence capture for each acceptance criterion, request-changes, reject, and approve-and-finish controls.
- Independent finish gate and auditable human decision events.
- Local Control Center and JSON-capable CLI workflows.

### Validation
- Product checks passed on the 1.0.0 source commit: Python 3.11–3.14, macOS, Windows, Docker E2E, and package build.
- CodeQL passed on the same source commit.
- Provider authentication and billing remain with the user's selected provider CLI and account.

## Product update — explicit human review decisions

- The local Control Center now supports human evidence entry per acceptance criterion, request-changes, reject, and approve-and-finish actions.
- The authenticated decision API requires a same-origin request, the one-time dashboard token, a bounded JSON body, and the per-task execution lock.
- Request changes records a reason and returns the task to `NEEDS_REVIEW`; rejection records a reason and closes the task without deleting its workspace.
- Approval is disabled until current signed checks, scope/integrity gates, and human acceptance are ready; it then invokes the independent `narrow finish` gate again.
- Security-sensitive path findings require a separate acknowledgement and a written human review note.
- Added `narrow review-action` for the same auditable decisions from the CLI. No API action launches an agent or executes commands.

## Product update — verification-gated repair continuation

- Before any automatic repair retry, `narrow drive` now rebuilds the deterministic review packet for the current workspace.
- Only plain verification failures with no blockers and no elevated risk flags can continue to another bounded attempt.
- Scope violations, unsafe links, credential-like changed paths, task/checkout invariant failures, and security-sensitive paths stop or escalate to human review.
- Escalations preserve the task and emit a sanitized `VERIFICATION_REPAIR_ESCALATED` event.
- A permitted retry does not imply approval or completion; fresh checks, human acceptance, and the independent `narrow finish` gate remain mandatory.

## Product update — one-command setup and security posture

### Added
- `narrow setup` detects a web, Python, data, or general project profile and prepares a read-only onboarding plan.
- Explicit `narrow setup --apply` saves the selected profile and adds only missing verification suggestions after interactive confirmation. Existing check commands are never overwritten and suggested checks are never executed.
- Saved profile scope defaults are validated against existing non-symlink top-level directories before they are used by `narrow start --auto-contract`.
- `narrow security` / `narrow security --json` exposes the distinction between task orchestration, agent runtime enforcement, and verification-container isolation.

### Safety boundaries
- Profiles do not enable Docker or claim to sandbox the agent.
- Verification sandbox remains off by default.
- Human acceptance remains required; passing configured checks cannot mark work complete by itself.

## 0.6.0b1 — 2026-11-03 (historical source build)

### Added
- Bounded project discovery for common language/build manifests; suggestions are reviewed before being written or run.
- Human-reviewed task-contract proposals, with optional loopback-only Ollama support.
- Opt-in Docker/Podman verification with a read-only container root, disabled networking, dropped capabilities, no-new-privileges, and resource limits.
- Opt-in POSIX/local-Docker Codex agent runtime with digest-pinned images, execution-scoped agent/proxy networks, a restricted egress proxy, short-lived credential mounts, and supervised teardown/recovery.
- Third-party provider-adapter extension point and expanded safety/setup documentation.

### Validation snapshot
- Merged-main workflow [#41778743003](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41778743003) passed all nine jobs on commit `979198b`: Ubuntu/Python 3.11–3.14, macOS, Windows repaired/blocked, real Docker proxy-only egress acceptance, and package build/clean install.
- CodeQL passed on the same commit in [run #41778742977](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41778742977).

### Scope and release notes
- This entry archives the 0.6.0b1 source snapshot that preceded product edition 1.0.0.
- The already-published v0.5.1 package is a earlier product generation distribution and remains unchanged.
- The Codex container profile is opt-in, POSIX/local-Docker only, and optional. Docker daemon and host kernel remain trusted; the mounted API key is readable inside the agent container. This is not a microVM or universal sandbox.
- Windows container supervision, Windows credential DACL handling, cross-platform fault injection, adversarial sandbox testing, and independent security review remain open.

## 0.5.1 — 2026-11-03 (earlier product generation)

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

## 0.5.0 — 2026-11-03 (earlier product generation)

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

## 0.4.0 — 2026-11-03 (earlier product generation)

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

## 0.3.0 — 2026-11-03 (earlier product generation)

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

## 0.2.0 — 2026-11-03 (earlier product generation)

### Added
- Native Claude Code and Codex CLI adapters for the bounded `drive` workflow.
- Structured JSONL event parsing, sanitized provider telemetry, and local provider session/thread ID continuity.
- Claude lifecycle hooks, provider detection, a read-only doctor command, and Work Receipt export.

### Changed
- Agent prompts, assistant content, tool inputs, and tool results are not stored in NARROW's event journal.
- Structured provider output is drained without placing task prompts in process arguments.

## 0.1.0 — 2026-11-03 (earlier product generation)

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
- A dual-homed proxy sidecar provisioner with explicit uplink gateway priority, inspected network attachments, sidecar readiness checks, and route-table verification.
- An opt-in `narrow drive --provider codex --container-agent` path that wires the policy manifest, credential lease, proxy capability mount, container command builder, and host supervisor into the bounded repair loop.
- A private mounted proxy-token file; the bearer capability is no longer placed in Docker CLI environment arguments.
- Linux Docker E2E smoke coverage and reproducible example Dockerfiles for the optional Codex agent and proxy images.

### Security and maturity limits
- The Docker proxy-only egress topology was subsequently validated by the merged-main container acceptance workflow.
- The current container profile is POSIX/local-Docker only. Windows credential-DACL support and Windows container supervision are not implemented.
- Docker's internal bridge has no default route for the agent and the entrypoint fails closed if one is present. The host bridge gateway remains reachable where host services permit it; the Docker daemon and host kernel are trusted.
- The mounted provider key remains readable by code running inside the agent. This is host-credential isolation, not protection from a malicious agent.
- This entry records the development sequence that led to the 1.0.0 product release.

## Product update — deterministic review gate

### Added
- `narrow review` and `narrow review --json` build a deterministic, read-only task evidence packet.
- Automated gates check task-contract integrity, unsafe workspace entries, allow/deny scope, change-count limits, tracked-diff whitespace hygiene, branch/HEAD invariants, original-checkout cleanliness, signed verification evidence bound to the current snapshot and configured command, and acceptance-evidence freshness.
- Credential-like changed paths are blockers without displaying or parsing their contents; security, auth, deployment, workflow, policy, billing, and runtime paths are flagged for focused human scrutiny.
- The local Control Center exposes the same report through an authenticated read-only endpoint and displays findings beside the diff preview.

### Approval boundary
- Deterministic evidence triage is not an LLM judge or semantic-correctness proof.
- Reports never approve, execute commands, mutate task state, commit, merge, or deploy.
- Human acceptance remains explicit; `narrow finish` independently rechecks completion invariants.
