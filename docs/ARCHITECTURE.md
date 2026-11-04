# NARROW Architecture — v0.5.0 legacy baseline (pre-Beta)

## Product model

NARROW owns the **task lifecycle**, not the intelligence of the model. The model proposes and edits; NARROW records intent, isolates repository work, watches observable boundaries, preserves task memory, and gates claims of completion.

```text
User intent
   |
   v
Task Contract -----> Local Event Journal <----- Decisions / Notes
   |
   v
Isolated Git Copy
   |
   +---- Provider Adapter (Claude / Codex / Generic)
   |          |
   |          +---- Structured Provider Events ----> Local Event Journal
   |          |
   |          v
   |     Filesystem / Git changes
   |          |
   +---- Scope + Base-Checkout Monitor
              |
              v
     Compact Task Handoff <---- Claude Session/Task/Subagent Hooks
              |
              v
     Bounded Repair Controller
              |
              v
   Configured Verification Commands
              |
              v
      Snapshot-bound Check Results
              |
              v
     Acceptance Confirmation
              |
              v
       VERIFIED_COMPLETE
              |
              v
    Read-only Local Control Center
```

## Components

### Task Contract

A task has a title, goal, explicit allowed path patterns, optional denied patterns, and one or more acceptance criteria. A task starts only from a clean original working tree. The current `HEAD` SHA is stored as the baseline.

### Isolation Manager

Each task gets a full local Git copy under the user's cache directory, a task branch inside that copy, and no configured remotes. The copy has an independent `.git` directory and object database; it does not share refs or configuration with the source repository. NARROW-managed Git calls strip inherited Git redirection variables, disable system/global configuration and fsmonitor, and the initial checkout skips Git LFS smudge downloads. LFS objects may need manual provisioning. NARROW rejects workspace symlinks that resolve outside the copy, hard-linked regular files, and special filesystem entries at task boundaries; live monitoring inspects changed paths. The original checkout is never used as the agent's working directory. NARROW does not merge the result.

### Scope Monitor

While the agent process is running, NARROW polls:

- changed tracked, untracked, and ignored paths relative to task `HEAD`;
- immutable fingerprints of NARROW-owned task, handoff, and prompt files;
- allowed and denied path patterns;
- original checkout `HEAD` and clean/dirty status;
- task workspace `HEAD` and branch;
- run duration and maximum changed-file count.

Out-of-scope or denied changes always stop the supervised process group. A final scan catches short-lived agents that exit before a polling cycle. This is best-effort process control, not a sandbox.

### Secondhand Memory

An append-only SQLite event journal records task creation, notes, decisions, agent lifecycle, scope events, and check results. `handoff` compiles the latest state into a portable Markdown packet. The packet is provider-neutral and intentionally avoids storing full chat transcripts. Claude task/subagent hooks add bounded lifecycle metadata without persisting task descriptions, assistant output, or raw tool errors.

### Local Control Center

A standard-library HTTP server binds only to `127.0.0.1`. It serves static assets locally and exposes bounded read-only JSON endpoints. Host and Origin checks, restrictive response headers, no CORS policy, and read-only methods reduce browser exposure. The UI uses text-only DOM updates and does not load remote assets. API responses deliberately exclude notes, prompts, check output, raw tool payloads, and provider conversation content.

### Verification Engine

Only named checks in `.narrow/config.json` can run. Each check is an argv array (not a shell string), is shown to the user, and requires interactive approval unless `--yes` is explicitly used. The `drive` command uses a native Claude Code or Codex CLI adapter, or a configured generic agent command, and re-runs checks after failure for a maximum of five attempts. Provider event streams are parsed into sanitized metadata. Claude hooks enforce a direct-file path gate for Write/Edit-family tools and persist a PreCompact handoff. Check output is incorporated into the next compact repair prompt. Any workspace mutation during verification stops the loop.

The engine hashes the changed workspace snapshot before and after a check. A check that mutates the workspace is not accepted as a successful verification. Successful results are bound to the exact snapshot hash they tested.

### Completion Gate

`finish` checks:

1. No denied or out-of-scope paths remain.
2. The task workspace is still on the task branch and baseline `HEAD` (agent-created commits were not accepted in the v0.5.0 baseline).
3. The original checkout is still unchanged and clean.
4. The authoritative task brief is intact and no unsafe filesystem links remain.
5. Every configured check passed against the current workspace snapshot.
6. Every acceptance criterion has explicit human confirmation and evidence.

The `drive` command alone never writes `VERIFIED_COMPLETE`; after checks pass it writes `READY_FOR_REVIEW`. Only the separate human acceptance and `finish` gate can write `VERIFIED_COMPLETE`.

## Storage

- `.narrow/state.sqlite3` — task and event state.
- `.narrow/config.json` — local verification configuration.
- `.narrow/handoffs/` — optional exported handoffs (created only when requested).
- User cache `.../narrow/worktrees/<repo-hash>/<task-id>/` — independent task repository copies (the internal database field retains the historical name `worktree`).

`.narrow/` and the task helper files are excluded through local Git exclude rules, not committed into user source repositories.

## Adapter boundary

The current provider adapter contract exposes operations equivalent to:

- `build_command(workspace, prompt_file, session_id, model, limits)`
- `parse_event(provider_jsonl_event)`
- `available()`
- `latest_provider_session_id(task)`
- `make_event_callback(store, task, adapter)`
- optional provider effort setting validated against an adapter-specific enum

Adapters must expose provider capability and permission mode. NARROW must never silently switch an agent into an approval-bypass mode. Unsupported provider behavior must be surfaced as unsupported, not guessed. Provider events are sanitized before persistence; raw conversation and tool payloads are intentionally not stored.


## Task-scoped execution lease (v0.5)

Before an agent, bounded drive loop, verification command, or completion check runs, NARROW acquires a non-blocking OS lock under `.narrow/locks/<task-id>.lock`. The lock is task-scoped, so unrelated tasks can proceed independently while duplicate operations on one task are refused.

The lock handle is passed to the first-level agent/check process. POSIX file locks are tied to the inherited open-file description and therefore remain held while that child keeps the descriptor open, including when the supervising NARROW process exits unexpectedly. Windows uses an inheritable file handle and still needs dedicated crash-recovery validation.

This protects against concurrent NARROW operations; it does not sandbox the child, prevent deliberate descriptor closure, or guarantee containment of detached grandchildren.


## Execution profiles and responsibility boundary

The adapter layer has three deliberately different trust profiles:

- Native Claude Code: NARROW configures the provider's required fail-closed shell sandbox, empty-by-default network allowlist, empty MCP configuration, and PreToolUse gates for selected direct file/search tools. The shell sandbox does not contain every provider tool/helper, and the hooks do not claim coverage of every possible action.
- Native Codex CLI: NARROW requests the provider's `workspace-write` sandbox and non-interactive no-auto-approval behavior. The installed Codex CLI owns the underlying enforcement.
- Generic command adapter and direct `run`/`resume`: NARROW supplies a task copy, monitor, and completion gate but does not create an OS sandbox. Processes retain the user's OS permissions unless the user independently wraps them in one.

Configured verification commands are separate child processes and also retain user permissions. They use argv arrays, timeouts, bounded output capture, and before/after snapshot checks, but these are not OS containment.

Human acceptance is a CLI-recorded assertion after manual inspection of the isolated diff and all untracked files. The finish gate checks the evidence and snapshot; it cannot verify that a person actually read the change. See [HUMAN_REVIEW.md](HUMAN_REVIEW.md) and [THREAT_MODEL.md](THREAT_MODEL.md).
