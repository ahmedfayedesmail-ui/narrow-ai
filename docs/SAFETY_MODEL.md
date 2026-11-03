# Safety Model

NARROW's primary safety objective is to reduce accidental damage and make uncertainty visible. The design should be conservative, observable, reversible, and honest about what it cannot enforce.

## Protected assets

- The user's original working tree and current branch.
- Uncommitted user work.
- Source code, local data, credentials, and environment variables.
- Correctness and integrity of the task result.
- The user's ability to inspect and decide before integrating changes.

## Non-negotiable invariants

1. Never start a task when the original checkout is dirty.
2. Never run the agent in the original checkout.
3. Never create an implicit unrestricted allowlist.
4. Never commit, merge, push, deploy, publish, or change remotes automatically.
5. Never call a task verified solely because an agent process exits with code 0 or says it is done.
6. Never call checks “passed” unless they ran successfully on the exact current snapshot.
7. Never hide failed checks, scope violations, monitor failures, or unresolved blockers.
8. Never silently enable agent permission-bypass flags.
9. Keep task notes and verification output local; do not send them to a NARROW service.
10. If NARROW cannot observe a safety-critical condition, fail closed and ask for review.

## Task execution lease (v0.5)

NARROW uses a non-blocking OS file lock per task. A second `drive`, `run`, `resume`, `verify`, or `finish` operation for the same task is refused while the lease is held. The first-level agent and verification child inherit the lock handle; on POSIX this keeps the lease held if the NARROW supervisor exits unexpectedly while the child remains alive. Different task IDs remain independent.

This lease does not confine process permissions and is not a sandbox. A detached descendant that deliberately closes or does not inherit the handle may outlive the lease. On Windows, the separate Job Object kill-on-close mechanism is intended to terminate ordinary job members if the supervisor exits; its forced-termination behavior remains subject to Windows adversarial validation.

## Core workspace protections inherited from v0.1

- Separate full Git copy, independent Git metadata, and no configured remotes.
- Clean-baseline requirement.
- Required allowlist and optional denylist.
- Process wrapper with periodic scope and repository-state checks.
- Best-effort process-group termination on detected violations and command timeouts; check output is streamed with a 64 KiB retained tail.
- Refusal of symlinks resolving outside the task workspace and hard-linked regular files, with preflight, live, and final scans.
- NARROW-managed Git calls ignore inherited Git redirection variables and system/global config, and disable repository fsmonitor hooks.
- No shell interpolation for command execution.
- Explicit user confirmation for verification commands.
- Snapshot hashes that invalidate stale test results after edits.
- Human confirmation with evidence for every acceptance criterion.
- Bounded repair loop (1–5 attempts); passing checks stop at `READY_FOR_REVIEW`.
- Initial clone/checkout disables system/global Git configuration and Git LFS smudge downloads to reduce hook/filter side effects.
- Refuse to follow a symbolic link for `.narrow/`, its state database, or the repository-local Git exclude file.
- Fingerprint the NARROW-owned task, handoff, and execution-prompt files and stop if an agent edits or removes them.

## Known limitations

A workspace is not an OS-level sandbox. A child process can still access any path and service permitted to the current user. A malicious or misconfigured agent may edit an absolute path outside the workspace, access the network, read environment secrets, start detached processes, or continue work after the wrapper exits. Polling is not instantaneous and can detect a violation only after it begins. A process can write to an absolute path, open a network connection, create and use a link between scans, or launch a detached/escaped process. Process-tree termination is best-effort across operating systems.

For untrusted repositories, agents, commands, or generated code, use a disposable VM or a container with read-only mounts, no host credentials, restricted network, and explicit resource limits. NARROW does not provision that sandbox in v0.2. The Git configuration isolation applies to NARROW-managed Git operations only; it does not constrain arbitrary shell, Python, package-manager, browser, or network activity launched by an agent or check.

## Safe use guidance

- Keep the AI provider's own approval prompts enabled.
- Do not pass permission-bypass flags.
- Start with narrow allowed paths.
- Do not put secrets in task goals, notes, decisions, or acceptance evidence.
- Configure only test commands you understand.
- Review `narrow inspect` and the actual diff before integration.
- Treat every agent-created file, command, and test as untrusted until reviewed.
- Manually cherry-pick or merge only after review.

## Completion semantics

`VERIFIED_COMPLETE` is deliberately narrow. It means configured checks passed for the exact current snapshot, acceptance criteria were explicitly confirmed by a human, task scope checks passed, and the original checkout remained unchanged. It does **not** mathematically prove correctness, security, performance, or absence of defects. Those claims require appropriate domain-specific tests and review.

## v0.2 provider protections

- Native Claude Code and Codex CLI adapters use documented structured-output interfaces rather than scraping private transcript files.
- Claude Code runs with the provider's default permission mode, and `--permission-prompts none` prevents unattended print-mode approval prompts from being silently approved.
- Codex runs with `workspace-write` sandboxing and `--ask-for-approval never` in non-interactive exec mode; permission-requiring actions are not silently approved. NARROW never adds `--yolo` or `--full-auto`.
- Claude's task-local profile requires the provider's OS shell sandbox, refuses startup/commands if the sandbox is unavailable, disables unsandboxed retries, denies shell reads outside working directories, and starts with a strict network allowlist that is empty by default.
- Claude's task-local `PreToolUse` hook gates direct Read/Glob/Grep roots to the task copy and checks direct Write/Edit-family targets against the allowlist and denylist, rejecting linked/reserved paths; hook failures block the tool call.
- Claude MCP servers are disabled for the automated profile, and the built-in tool list excludes WebSearch/WebFetch. This avoids loading user or repository MCP servers into this non-interactive workflow.
- Common credential files and secret-like environment-variable names are denied to Claude's sandboxed shell subprocesses. This is a finite list/pattern discovery, not a proof that every credential has been found.
- Claude's `PreCompact` hook writes a fresh local handoff. The Stop hook is telemetry-only and never keeps an agent running behind the user's back.
- Provider event parsing stores only selected metadata; full prompts, assistant messages, tool inputs, and tool results are not persisted by native adapters.
- Generic `narrow run` preserves terminal streams; provider JSONL capture is enabled only for the structured adapter.

These protections add an OS-enforced shell boundary for Claude on supported macOS/Linux/WSL2 hosts, but Claude's built-in file tools, hooks, and helper processes remain outside that shell sandbox. The direct file-tool gate and disabled MCP configuration narrow that remaining surface; they do not make NARROW equivalent to a complete disposable VM or container. The Codex profile relies on Codex's own workspace-write sandbox. Neither provider has yet passed a maintainer-run cross-platform security validation matrix.


## v0.4 Control Center and modern provider controls

- The Control Center is read-only, binds only to loopback, rejects untrusted Host/Origin values, uses restrictive browser headers, and ships local-only static assets. It is an observability surface, not a remote command interface.
- Dashboard APIs return sanitized task summaries, path names, check metadata, and event titles/timestamps; they do not return prompts, notes, raw provider output, or raw check logs. Treat the dashboard as local information that may still contain project titles and paths.
- Claude TaskCreated/TaskCompleted and subagent lifecycle hooks record bounded IDs/types only. User-authored descriptions, task subjects, assistant messages, raw errors, and commands are not persisted by those hooks.
- Claude Agent/task tools are opt-in and disabled by default. Enabling them does not add a separate sandbox; child agents share task workspace permissions.
- Effort overrides are validated enum values passed through native provider CLI flags/config. Provider model/account support is authoritative; NARROW does not invent compatibility.
- Provider version probes invoke only `--version` when explicitly requested.


## Execution-profile boundary (v0.5.0)

The word "isolated" in NARROW means a separate Git working copy. It does not mean a process-level permission boundary.

- `narrow drive --provider claude` uses NARROW's native Claude profile: required fail-closed shell sandbox settings, an empty-by-default network allowlist, an empty MCP config for that profile, and fail-closed PreToolUse gates for selected direct file/search tools. Claude's shell sandbox does not cover every provider tool/helper; the hooks cover only the documented tool families.
- `narrow drive --provider codex` selects the Codex CLI's `workspace-write` sandbox and non-interactive no-auto-approval mode. Its exact boundary remains provider-version/configuration dependent.
- `narrow drive --agent NAME` is a generic command adapter. NARROW does not install an OS sandbox for it. `narrow run` and `narrow resume` are also direct command wrappers and do not configure the native provider profile.
- Every configured verification command runs as an executable with the invoking user's permissions. It is shown for confirmation by default; `--yes` and `--yes-checks` intentionally skip that prompt. Timeouts and snapshot detection are not a sandbox.

For untrusted code, use a disposable VM/container with restricted mounts, no host credentials, limited network, CPU/memory/process limits, and a low-privilege user. NARROW v0.5.0 does not create that outer boundary.

## Explicit human-review procedure

1. Run `narrow status --task <id>` and `narrow inspect --task <id>` from the original checkout.
2. Use the displayed isolated workspace path to review `git status --short --untracked-files=all`, `git diff --check`, the full tracked diff, and every untracked file.
3. Run reviewed configured checks against the current snapshot.
4. Record each criterion with `narrow accept --task <id> --criterion N --evidence "..."`.
5. Run `narrow finish --task <id>`. Any later edit makes previous check/acceptance evidence stale.

This procedure is documented in [HUMAN_REVIEW.md](HUMAN_REVIEW.md). The command records the user's assertion; it cannot prove that a human genuinely inspected the diff. NARROW never merges or integrates the task automatically.


## Experimental verification container profile (feature branch)

An optional Docker/Podman wrapper is being developed for configured verification commands. When enabled, it uses a pre-existing local image, no container network, a read-only container root, a read-only task-workspace bind mount by default, a writable temporary filesystem, dropped Linux capabilities, no-new-privileges, and CPU/memory/PID limits. The image is not pulled automatically. The profile is opt-in and defaults to off for backward compatibility.

The wrapper is applied to verification commands only. It does not wrap the AI provider process, Claude/Codex helpers, generic agent commands, or arbitrary `narrow run` commands. It therefore does not create the requested universal default isolated executor and is not a complete host boundary. Docker/Podman daemon and host kernel remain trusted.

Project auto-discovery reads manifests and file names but never executes suggested test commands. Contract proposals from Ollama are sent only to the local loopback endpoint and may include a bounded selection of project source files. Proposals never create a task or approve paths automatically; a human must review and confirm them.

External provider plugins are imported Python code from the user's environment. They are trusted extensions, not a security sandbox. The dashboard intentionally remains read-only until an authenticated mutation design has been reviewed. Windows atomic Job membership is implemented for supported Windows versions, but process-tree crash containment is not claimed as validated until the Windows hard-kill and descendant tests run.


## Feature-branch hardening notes

- The dashboard still has no mutation routes. A random per-launch token is now required on JSON API requests; the browser receives it through a URL fragment, stores it in sessionStorage, and sends it in `X-NARROW-Token`. Host and Origin checks remain in place. A valid token does not enable POST/PUT/DELETE.
- On Windows 10 / Windows Server 2016 and newer, the first-level process is created suspended with `PROC_THREAD_ATTRIBUTE_JOB_LIST`, so Job membership is established by the same `CreateProcessW` call rather than by a post-creation assignment. NARROW confirms `IsProcessInJob` before resuming the initial thread. The non-inheritable Job handle uses kill-on-close; ordinary descendants inherit membership unless they use a permitted breakaway mechanism (none is enabled here). If NARROW is forcibly terminated before or after resume, the child is already a Job member and is subject to kill-on-close. Unsupported/incompatible Job setups fail closed. Broker-created processes and daemon-managed external containers are not guaranteed to be covered. Forced-termination and deep-descendant Windows tests remain a release gate.
- Verification records are HMAC-SHA256 signed with a local per-project key kept outside the task worktree in the user's NARROW cache. The signature binds the saved output digest, command, exit code, duration, timestamp, and snapshot. This detects SQLite-only tampering but cannot defeat a compromised same-user host or a stolen/replaced key.
- Contract proposals and human-approved contracts are recorded as separate versions with field-level differences. Active task contracts remain immutable; changing scope requires a new task.
- Optional package-manager caches are private and task-scoped. NARROW does not mount the developer's home package-manager caches or credential directories.
