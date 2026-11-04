# NARROW v0.2.0 legacy pre-Beta release (historical)

NARROW v0.2.0 adds native Claude Code and Codex CLI support to the local-first scope, memory, and completion engine.

## Highlights

- `narrow providers` detects supported provider executables on the current PATH.
- `narrow drive --provider claude` and `narrow drive --provider codex` provide native command construction, structured event streams, session/thread continuity, and bounded repair.
- Claude Code gets task-local hooks for direct file-write scope checks and pre-compaction handoff saves.
- Provider metadata is journaled locally without retaining full prompts, assistant responses, tool arguments, or tool results.
- Generic `narrow run -- <command>` keeps inherited terminal streams so interactive commands remain usable.
- Sanitized, snapshot-aware Work Receipt export via `narrow report --format markdown|json`.
- CI and local tests cover adapter command construction, event privacy, hook settings shape, path-gate behavior, JSONL output handling, and Work Receipt generation.

## Upgrade

From a source checkout:

```bash
python -m pip install --upgrade .
```

The `.narrow` state schema remains at version 1. Existing tasks and local journals are not migrated or deleted.

## Safety and known limits

v0.2.0 was a legacy pre-Beta release. The Git task copy is not an OS sandbox. Claude's hook covers only direct file-write/edit tools; Codex has no NARROW-native PreToolUse hook yet. Shell commands and MCP tools can perform external effects with the invoking user's permissions. NARROW does not commit, merge, push, deploy, or auto-accept tasks.

A local passing test suite is not a substitute for provider end-to-end tests, cross-platform testing, and independent security review. See [PROVIDERS.md](PROVIDERS.md), [SAFETY_MODEL.md](SAFETY_MODEL.md), and [THREAT_MODEL.md](THREAT_MODEL.md).
