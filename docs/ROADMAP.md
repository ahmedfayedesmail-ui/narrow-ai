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

## v0.4 — Control surfaces and modern provider controls (implemented; Alpha)

- Read-only loopback Control Center for task overview, scope, checks, acceptance, and sanitized activity.
- Bounded task portfolio command and JSON output.
- Claude Code and Codex CLI reasoning-effort overrides with provider-native configuration mechanisms.
- Optional Claude Agent/task tool support, dynamic multi-agent mode, and minimized task/subagent lifecycle telemetry.
- Provider CLI compatibility diagnostics.

## v0.5 — Task execution coordination (implemented; Alpha)

- Single-flight OS lock per task across `drive`, direct `run` / `resume`, `verify`, and `finish`.
- First-level agent and verification processes inherit the lease handle. POSIX regression coverage proves the lock remains held after the NARROW-side descriptor closes while the child still has its inherited descriptor.
- Lock-path checks reject path traversal, symlinked lock directories, and hard-linked lock files.
- Public source tree and a versioned source archive are published together.
- Linux CI validates the source tree and extracted archive on Python 3.11–3.14. Windows Server 2025 / Python 3.13 CI exercises cross-process task-lock contention and CLI startup.

**Remaining exit gate:** validate Windows inherited-handle crash behavior, authenticated Claude/Codex sessions, detached process behavior, macOS, and a real sandbox boundary. The execution lease is coordination, not an OS sandbox.

## v0.6 — Stronger execution isolation (planned)

- Optional disposable Docker/Podman sandbox with a read-only source mount and writable task workspace only.
- Explicit network allow/deny policy, no host credentials mounted by default, CPU/memory/process/wall-clock limits.
- Safe dependency-cache strategy and platform-specific containment tests.
- Clear provider capability matrix: which provider tools execute inside the sandbox and which hooks or direct file operations remain outside it.

**Exit gate:** independent review of container boundaries and tests proving that agent processes cannot modify the original checkout or access denied host paths under the supported configuration.

## v0.7 — Deeper completion intelligence (planned)

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
