# Native Provider Integrations (v0.4.0)

NARROW integrates with locally installed provider CLIs. Provider authentication and billing remain entirely under the provider's own account and local configuration. NARROW does not request API keys, inspect credential stores, upload source code, or call provider APIs directly.

## Claude Code

NARROW launches Claude Code in print mode with structured stream output and a task-local settings file. The adapter uses documented CLI options:

- `--print`
- `--output-format stream-json --verbose`
- `--permission-mode default`
- `--permission-prompts none`
- `--max-turns N`
- optional `--model`, `--effort`, and `--resume SESSION_ID`
- optional `--max-budget-usd` client-side spend estimate/cap
- `--settings <task-local-settings.json>`
- `--strict-mcp-config --mcp-config <empty-task-local-mcp.json>`
- restricted `--tools` set and `--disallowedTools mcp__*`
- task prompt over standard input rather than a process argument

`--permission-prompts none` is deliberately fail-safe in non-interactive print mode: permission-requiring actions are denied instead of silently approved. NARROW's `providers --versions` and `doctor --versions` commands run only the detected CLI's `--version` command. They report a telemetry upgrade recommendation when Claude is older than v2.1.290 because NARROW uses agent metadata exposed by newer task/subagent lifecycle hooks. The base provider may still launch on older versions, but full task/subagent telemetry is not guaranteed.

### Reasoning effort

NARROW accepts `--effort low|medium|high|xhigh|max` for Claude Code and passes the value with the provider's `--effort` flag. `max` is not available on every model/account. Other effort levels may also be capped or unsupported by a selected model or organization policy. NARROW intentionally does not override the provider default when `--effort` is omitted. The provider CLI is authoritative for the final model/effort combination.

### Fail-closed shell sandbox

The task-local settings require Claude Code's built-in OS sandbox for shell commands:

- `sandbox.enabled=true`
- `sandbox.failIfUnavailable=true` — do not silently fall back when the sandbox cannot initialize.
- `sandbox.allowUnsandboxedCommands=false` — disable the unsandboxed retry escape hatch.
- `permissions.blockReadsOutsideWorkingDirectories=true` for sandboxed commands.
- `sandbox.network.strictAllowlist=true` with an empty allowed-domain list by default.
- Common credential files and secret-like environment variable names are denied to sandboxed subprocesses.

A repository owner may add the minimum required hosts to `.narrow/config.json` using `claude_network_allowed_domains`. NARROW validates these as hostname entries (not URLs). Do not use broad wildcards. With an empty list, shell commands requiring network access, such as package installation, fail closed until the required registry host is deliberately added.

This boundary is provider-defined and applies to Claude's shell commands and child processes. Claude documents that built-in file/web tools, hooks, local MCP servers, plugins, and helper processes can run outside the Bash sandbox. NARROW therefore limits the enabled built-in tool set, disables MCP for this automated profile, and adds a fail-closed PreToolUse gate for selected direct file reads/searches/writes. A container or VM is still required for stronger isolation of all provider processes.

### Hooks installed by NARROW

The generated task-local settings register:

| Hook | Behavior |
|---|---|
| `SessionStart` | Adds the current task contract and boundaries as provider context. |
| `PreToolUse` | Blocks selected direct `Read` operations outside the isolated task copy; confines `Glob`/`Grep` roots; blocks direct `Write`, `Edit`, `MultiEdit`, and `NotebookEdit` targets outside the allowlist, inside deny patterns, or traversing unsafe linked/reserved paths. |
| `PostToolUse` | Records tool name and target-path metadata only. |
| `PostToolUseFailure` | Records tool name and bounded failure metadata, not raw error text or command content. |
| `PreCompact` | Writes a fresh `NARROW_HANDOFF.md` from the local task journal before compaction. |
| `SubagentStart` / `SubagentStop` | Records a bounded agent ID/type and lifecycle event. Assistant output is not persisted by these hooks. |
| `TaskCreated` / `TaskCompleted` | Records bounded task/agent identifiers and whether a subject was present; task subject/description is deliberately not persisted. |
| `Stop` | Records the stop signal, but never blocks Stop or creates a hidden agent loop. |
| `SessionEnd` | Records session exit metadata. |

Hook errors for the direct-file gate are configured to fail closed. Other lifecycle telemetry hooks are non-blocking and do not prevent the provider from ending a task. The hooks protect selected direct tools; they do not inspect arbitrary content semantics or all possible path syntaxes. Bash uses Claude's separate OS sandbox. MCP is disabled for this automated profile. Local provider/plugin helper processes and any process started outside those boundaries remain outside NARROW's guarantee.

### Optional subagents

Claude Agent/task tools are omitted from the default tool list. A user can explicitly opt in with `narrow drive --provider claude --allow-subagents`. Subagents share the same task workspace, the same allow/deny scope, and user-level process permissions. NARROW's hooks and final workspace inspection observe selected file operations, but **this option does not create a separate sandbox per subagent**. Do not enable it for untrusted projects or agents until independent subagent isolation and escape tests exist.

Claude Code also exposes a dynamic multi-agent workflow mode via `--effort ultracode` (distinct from the normal reasoning-effort levels). NARROW exposes this as `narrow drive --provider claude --allow-subagents --ultracode`. It is a higher-autonomy opt-in, cannot be combined with `--effort`, remains subject to model/account availability, and is not enabled by default. The Claude CLI remains responsible for model compatibility and may reject the mode.

## OpenAI Codex CLI

NARROW launches Codex through its non-interactive `exec` interface with JSONL events. The command uses:

- `codex exec --json`
- `--cd <isolated-task-workspace>`
- `--sandbox workspace-write`
- `--ask-for-approval never` — no interactive approval dialog; actions outside the selected sandbox remain denied
- optional `--model`
- optional `--config model_reasoning_effort="<level>"`
- `codex exec resume <THREAD_ID>` when a previous attempt exposed a valid thread ID
- `-` as the prompt-from-stdin sentinel

Codex reasoning effort values accepted by NARROW are `low`, `medium`, `high`, and `xhigh`. Availability depends on the selected model and provider CLI version. NARROW does not set the override when omitted.

NARROW does not use `--yolo`, `--dangerously-bypass-approvals-and-sandbox`, or equivalent bypass modes. The JSONL parser stores thread/turn/item metadata and safe error categories, not raw prompt or tool payloads.

Codex's own sandbox and approval controls remain authoritative. NARROW does not currently install a Codex-native pre-tool file-path hook; its scope enforcement remains a combination of task instructions, workspace monitoring, and final inspection.

## Bounded repair, session continuity, and cross-provider handoff

NARROW runs a fixed number of agent attempts (maximum five), and every attempt is followed by configured verification commands. Failed checks can trigger a new provider attempt with the latest NARROW handoff and, when available, the same provider session/thread ID. If the provider session ID is absent, invalid, or belongs to another provider, NARROW starts a new session using the handoff instead of guessing an ID.

```bash
narrow handoff
narrow resume -- codex
# or
narrow drive --provider codex
```

The handoff is generated from the local task journal, contract, changed paths, and verification evidence. It is not a transcript and cannot recover decisions that were never recorded in NARROW.

## Event privacy

NARROW intentionally excludes full prompts, assistant text, raw tool inputs, raw tool results, environment values, and raw provider error messages from provider event records. It may record provider name, event category, model label, tool names, session/thread ID, turn counts, duration, and reported cost metadata. Claude task/subagent hooks persist metadata rather than user-authored task descriptions or agent output. Session/thread IDs are stored locally in `.narrow/state.sqlite3`; treat the NARROW state directory as private and do not commit it.

Non-JSON provider diagnostics may be displayed in the user's terminal but are not copied to the event journal by the structured-output adapter.

## Support and verification status

- Claude Code: adapter, command construction, hook privacy, and policy configuration are covered by local tests; actual authenticated provider E2E, current-model effort support, and subagent behavior remain unverified.
- Codex CLI: command construction, effort override shape, JSONL event parsing, and session-ID handling are covered by local fake-event tests; actual authenticated provider E2E remains unverified.
- Claude shell sandbox: generated strict settings and policy hooks are unit-tested, but real sandbox initialization, credential blocking, network enforcement, and escape resistance require platform-specific tests.
- Windows-native Claude shell sandbox is not claimed; follow the provider's current platform support guidance. Windows/macOS NARROW behavior is not yet validated by maintainers.
- Android/Termux is not a supported target yet.

Official references:

- Claude Code CLI reference: https://code.claude.com/docs/en/cli-reference
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- Claude Code sandbox: https://code.claude.com/docs/en/sandboxing
- Claude Code effort guidance: https://platform.claude.com/docs/en/build-with-claude/effort
- Codex CLI reference: https://developers.openai.com/codex/cli/reference
- Codex CLI configuration: https://developers.openai.com/codex/config-basic
