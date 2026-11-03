# Product Specification

## Product promise

NARROW makes AI-assisted work **focused, resumable, and verifiable**. It does not promise that an AI agent will always succeed. It promises to make task intent and boundaries explicit, preserve the operational context, surface observable drift, and refuse to label unverified work as complete.

## Primary user

A person who delegates multi-step work to one or more AI agents and needs to keep control of the task, code, decisions, and quality. The first release targets software repositories because Git supplies a practical isolation boundary and tests can provide measurable evidence.

## v0.4 product surface

- Read-only local Control Center on loopback for task portfolio, live status, scope summary, acceptance counts, sanitized events, and snapshot-current check results.
- `narrow tasks` for bounded human-readable or JSON task listings.
- Model effort overrides for native Claude Code and Codex CLI adapters, with provider/model support left authoritative.
- Optional Claude task/subagent tools and dynamic multi-agent mode with minimized lifecycle metadata; all are opt-in, and subagents share the task workspace and permissions.

## Daily workflow

1. Describe the task once.
2. Define allowed files and acceptance criteria.
3. Launch the user's chosen AI agent in an isolated repository copy.
4. Let NARROW watch for repository-level scope drift.
5. Save important decisions and blockers in a local journal.
6. Switch sessions or providers using a compact handoff.
7. Optionally run a bounded agent → verification → repair cycle using an explicitly configured command adapter.
8. Inspect the result and confirm acceptance criteria.
9. Manually integrate the work.

## Four product engines

### Scope Engine

- Task contract with objective, allowed paths, denied paths, and acceptance criteria.
- Monitor tracked, untracked, and ignored repository paths.
- Detect changes to the original checkout and task branch.
- Fail closed and stop the supervised process on boundary violations; scope stopping cannot be disabled for an automated run.
- Refuse existing external symlinks and hard-linked files in the workspace; monitor and rescan for them.
- Report uncertainty and monitoring failures rather than assuming safety.

### Continuity Engine (SECONDHAND)

- Append-only local event journal.
- Structured notes and decisions.
- Provider-neutral handoff packet.
- Provider session/thread ID storage for supported native adapters.
- Claude SessionStart and PreCompact hooks for task context and fresh handoffs.
- Claude task/subagent lifecycle metadata and tool failure categories without storing user-authored descriptions or raw error output.
- Sanitized structured event memory; full prompts and tool payloads are intentionally excluded.

### Completion Engine

- Configured test/check commands with explicit approval.
- Check results bound to exact workspace snapshots.
- Human acceptance evidence.
- bounded repair cycles using a configured command adapter, a maximum five-attempt budget, and snapshot-aware checks.
- Successful checks transition only to `READY_FOR_REVIEW`; human acceptance remains a separate gate.
- Clear states: `ACTIVE`, `NEEDS_REVIEW`, `BLOCKED`, `VERIFIED_COMPLETE`.

### Agent Adapter Layer

- v0.2+ has native Claude Code and Codex CLI adapters plus a generic user-configured command adapter.
- v0.4 passes optional reasoning-effort settings through each provider CLI; unsupported model/account combinations remain provider errors rather than guessed settings.
- Claude uses structured stream output, a required provider shell sandbox, strict network allowlisting, empty MCP config, stdin prompts, and fail-closed direct file/read hooks.
- Codex uses structured JSONL output, workspace-write sandbox mode, non-interactive `never` approval semantics (no auto-grant), stdin prompts, and resumable thread IDs.
- Every adapter must disclose supported capabilities and permission mode. No adapter may silently bypass provider approvals or sandbox settings.

## Product principles

- Local-first and no mandatory API key.
- Model/provider neutral core.
- No NARROW cloud service required for the core workflow.
- No hidden source-code upload or telemetry.
- Human authority over scope expansion, external effects, and integration.
- Exact evidence over success messages.
- Bounded automation over endless agent loops.
- Useful on a normal developer machine; stronger sandboxing is opt-in until the project can maintain it safely.

## Success metrics

The first public beta should measure:

- Percentage of tasks whose original checkout remains unchanged.
- False-positive rate of scope alerts.
- Rate of stale verification results correctly invalidated.
- Percentage of sessions successfully resumed from handoffs.
- Number of tasks marked verified that later require rework (target: as close to zero as possible).
- Median time saved during provider/session switching.
- Crash/recovery rate and cross-platform test coverage.

Do not optimize for number of agent actions, tokens, or “autonomous” minutes. Optimize for trustworthy task outcomes.
