# Changelog

All notable changes to NARROW are documented here.

## 0.4.0 — 2026-11-03 (Alpha)

### Added
- Local, loopback-only, read-only Control Center with responsive task, scope, acceptance, verification, and sanitized activity views.
- `narrow tasks` for active/all task listing and bounded, sanitized JSON output.
- Cross-provider reasoning-effort controls for Claude Code and Codex CLI, subject to provider model/account support.
- Claude task and subagent lifecycle telemetry with metadata minimization.
- Explicit `--allow-subagents` opt-in and a separate Claude `--ultracode` opt-in.
- Provider version compatibility diagnostics for newer Claude task/subagent telemetry.

### Changed
- Control Center APIs expose sanitized summaries only; prompts, task notes, tool arguments/results, and raw check output are not returned.
- Documentation clarifies that an isolated Git copy is not an OS sandbox and processes retain the invoking user's permissions.

### Validation and limitations
- Local QA: 79 tests passed on Linux, Python 3.13, Git 2.47.
- CI matrix configured for Ubuntu with Python 3.11–3.14.
- Authenticated provider E2E, Windows/macOS/Android validation, independent sandbox escape tests, and external security review remain outstanding.
- NARROW does not automatically commit, merge, push, deploy, or mark a task accepted on behalf of a human.

## 0.3.0 — 2026-11-03 (Alpha)

- Added Claude strict shell sandbox enforcement with fail-closed behavior, selected direct-file path gates, and reduced tool/network exposure.
- Added non-interactive Codex execution in workspace-write mode.
- Added bounded drive/verify/repair behavior, snapshot-bound evidence, and human acceptance gates.
- Clarified that NARROW's Git-copy isolation is not OS-level containment.

## 0.2.0 — 2026-11-03 (Alpha)

- Added native Claude Code and Codex CLI adapters, structured JSONL event parsing, local session/thread continuity, lifecycle hooks, a read-only doctor command, and Work Receipt export.
- Reduced persisted provider telemetry to metadata; prompts, assistant content, tool inputs, and tool results are not stored in the NARROW journal.

## 0.1.0 — 2026-11-03 (Alpha)

- Added local task contracts and SQLite event journal.
- Added a separate Git copy per task with remotes removed, explicit path allow/deny policy, generic CLI adapter, handoff, explicit check approval, and bounded repair.
- Added snapshot-bound checks, human acceptance gate, and initial security/threat model.
