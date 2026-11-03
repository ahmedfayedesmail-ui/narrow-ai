# NARROW v0.4.0 Alpha — Release Notes

Release date: 2026-11-03

## Highlights

NARROW v0.4.0 adds a local task Control Center, task portfolio CLI, provider effort controls, and opt-in Claude Agent/task lifecycle telemetry.

### Local Control Center

Run `narrow dashboard` from the original repository. The server binds to `127.0.0.1`, is read-only, and ships all assets locally. It shows task status, changed-path counts, allowed/outside/denied paths, unsafe link counts, acceptance progress, latest verification results, and sanitized activity. It does not expose task notes, prompts, tool input/output, or raw verification logs.

### Modern provider controls

`narrow drive --provider claude --effort xhigh` passes the requested effort to Claude Code. Claude `max` is accepted by the CLI but the selected model/account must support it.

`narrow drive --provider codex --effort high` uses the documented Codex CLI `--config` override for `model_reasoning_effort`. The selected Codex model must support the chosen level.

Omitting `--effort` preserves provider defaults. NARROW does not assume that every model/account supports every effort level.

### Claude task and subagent telemetry

Task and subagent lifecycle hook records are metadata-only. Task descriptions, task subjects, assistant output, error text, command strings, and tool results are intentionally omitted from persistent hook telemetry. Claude Agent/task tools remain disabled unless `--allow-subagents` is explicitly provided.

## Validation

- Local QA: 79 tests passed using Python 3.13 on Linux with Git 2.47.
- CI matrix configured for Python 3.11, 3.12, 3.13, and 3.14 on Ubuntu.
- Provider tests use fake CLI outputs for deterministic adapter and privacy testing; authenticated live provider tests remain outstanding.

## Important limitations

- NARROW's isolated Git copy is not an OS sandbox.
- Claude hooks cover selected documented tools and cannot guarantee control over every tool/helper process.
- Codex's provider sandbox is authoritative; NARROW does not install a Codex pre-tool hook.
- Subagents share the same task workspace and permissions.
- The dashboard is a local observability surface, not a remote-control panel.
- Human review remains required; no automatic commit, merge, push, publish, or deploy is performed.
