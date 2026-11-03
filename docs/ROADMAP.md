# Roadmap

The roadmap is deliberately gated by safety evidence. A milestone is not complete because a demo looks good; it must pass its stated tests and failure scenarios.

## v0.1 — Safety-first foundation (implemented)

- Local task contract and SQLite event journal.
- Separate Git copy per task, with remotes removed.
- Explicit path allowlist and denylist.
- CLI agent wrapper with process monitoring and bounded runtime.
- Provider-neutral handoff and manual resume.
- Explicitly approved verification commands.
- Snapshot-bound checks and human acceptance gate.
- Bounded generic agent → checks → repair cycle.

## v0.2 — Native provider adapters (implemented; Alpha)

- Claude Code and Codex CLI adapters using structured output and resumable session/thread IDs.
- Sanitized provider event journal and fake-event conformance tests.
- Provider detection, opt-in version probes, read-only doctor diagnostics, and Work Receipt export.

## v0.3 — Native agent safety hardening (implemented; Alpha)

- Claude strict shell sandbox required; fail closed if unavailable and prohibit unsandboxed retries.
- Deny shell reads outside the task working directory and common credential files/secrets to sandboxed subprocesses.
- Deny shell network access by default; allow only explicit narrow domain entries from local config.
- Disable MCP for automated Claude sessions and narrow the built-in tool set.
- Fail-closed hooks for selected direct file reads/searches/writes.
- Codex non-interactive exec with `workspace-write` and approval-deny mode.

## v0.4 — Control surfaces and modern provider controls (Alpha implementation)

- Read-only loopback Control Center for task overview, scope, checks, acceptance, and sanitized activity.
- Bounded task portfolio command and JSON output.
- Claude Code and Codex CLI reasoning-effort overrides with provider-native configuration mechanisms.
- Optional Claude Agent/task tool support, dynamic multi-agent mode, and minimized task/subagent lifecycle telemetry.
- Provider CLI compatibility diagnostics.

**Remaining exit gate:** run authenticated end-to-end tests on currently supported provider CLIs and models; test hook behavior, subagent completion semantics, interruption/restart recovery, and budget behavior; obtain an independent security review.

## v0.5 — Stronger execution isolation (planned)

- Optional disposable Docker/Podman sandbox with a read-only source mount and writable task workspace only.
- Explicit network allow/deny policy, no host credentials mounted by default, CPU/memory/process/wall-clock limits.
- Safe dependency-cache strategy and platform-specific containment tests.

**Exit gate:** independent review of container boundaries and tests proving that agent processes cannot modify the original checkout or access denied host paths under the supported configuration.

## v0.6 — Deeper completion intelligence (planned)

- Explicit task state machine and dependency graph.
- Dependency-aware check selection, followed by full required verification before completion.
- Evidence-linked failure classification and repair proposals.
- Stronger time, process, and command budgets across multi-agent task plans.
- Automatic escalation on repeated failure, scope expansion, missing permissions, destructive operations, or ambiguous requirements.
- “Blocked” as a valid terminal outcome; never fabricate success.

**Exit gate:** fault-injection tests prove that loops stop at every configured budget, stale evidence is invalidated, and all external/destructive actions require explicit authorization.

## v1.0 — Public stable release (planned)

- Documented support matrix.
- Reproducible builds and signed releases.
- Security contact and vulnerability response process.
- Migration/backward-compatibility policy for local state.
- Independent security review of autonomous execution path.
- Clear guarantee matrix distinguishing verified behavior from best-effort monitoring.
