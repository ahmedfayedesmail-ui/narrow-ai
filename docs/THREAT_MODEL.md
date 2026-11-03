# Threat Model (v0.5 Alpha)

## Trust boundaries

- **User:** chooses the task, scope, agent, verification commands, and acceptance evidence.
- **NARROW CLI:** trusted to create task metadata, supervise the launched process, record state, and apply completion gates.
- **AI agent:** untrusted with respect to correctness; it may misunderstand the request, over-edit, stop early, or attempt unsafe commands.
- **Repository content:** untrusted input. Build scripts, test scripts, hooks, package managers, and project instructions may execute code.
- **External services:** outside NARROW's control. Agent CLIs may transmit prompts or repository content according to their own provider settings.

## Threats and mitigations

| Threat | v0.2 mitigation | Residual risk |
|---|---|---|
| Agent edits wrong project files | Separate workspace, strict path allowlist, live/final scans, refusal of pre-existing external symlinks/hard links; Claude direct Read/Glob/Grep and Write/Edit-family tools receive fail-closed path gates; Claude shell commands run inside required OS sandbox; Codex uses workspace-write sandbox | Claude's file tools and hooks are outside its Bash sandbox; Codex provider sandbox is not a complete VM; residual OS/provider edge cases remain |
| User's uncommitted work is overwritten | Refuse dirty original checkout; agent does not run there | External commands may still access the original path |
| Agent commits or switches branches | Detect changed task `HEAD`; stop and block finish | A commit may already exist on the isolated task branch |
| Agent says “done” with failing tests | Completion requires configured checks and human acceptance | Tests can be incomplete or incorrect |
| Stale test result after later edits | Check results bound to snapshot hash | Files outside the tracked/untracked scan may be missed in unusual setups |
| Lost context between sessions | Local append-only journal, Markdown handoff, provider session/thread IDs, Claude SessionStart/PreCompact hooks | Unrecorded decisions from chat are not recoverable; provider IDs may be absent or expire |
| Secret pasted into notes | Common-pattern redaction | Redaction is heuristic; never paste secrets |
| Dangerous check command | Explicit config, command shown, interactive confirmation, no shell | The command still has user permissions and can have side effects |
| Agent runs indefinitely | Run timeout, provider turn cap, bounded repair attempts, and process-group stop | Detached or escaped processes may survive; provider-side work may continue outside the supervised process on some platforms |
| Dashboard leaks operational data to remote pages | Loopback-only bind, strict Host/Origin checks, no CORS, read-only routes, restrictive headers, no remote assets, and sanitized response schemas | Local browser extensions, same-device users, malware, and browser vulnerabilities remain outside the protection boundary; dashboard includes project titles and path names |
| Task/subagent telemetry captures user content | Hook payloads are reduced to bounded IDs/types, subject-present flags, and event names; no descriptions or assistant content are persisted | Future provider hook schema changes may add fields; conformance tests and strict field allowlists must be updated before enabling them |
| Model effort override is unsupported | Adapter enum validation and provider-native validation; omit effort by default | Model/account/organization availability varies; provider may cap, reject, or ignore an effort |
| Checkout triggers a configured Git hook/filter | NARROW-managed Git calls disable system/global config, inherited Git redirection, and fsmonitor; initial clone skips Git LFS smudge | Local repository filters and arbitrary non-Git build scripts remain a trust boundary; agent commands are not OS-sandboxed |
| NARROW metadata path is redirected | Refuse symbolic links/hard links for sensitive metadata files; atomically replace NARROW-owned helper files | Host filesystem, parent directories, and same-user races are still trusted |
| Monitor fails | Stop the supervised run when repository checks fail; provider event-journal failures stop the adapter run | OS/process races remain possible |

## Out of scope for v0.4

- Defending against a fully malicious local user or a compromised OS.
- A complete container/VM boundary around every agent process, provider file tool, hook, plugin, helper, and check command.
- Guaranteeing process-tree containment on every platform.
- Preventing arbitrary absolute-path writes or network access without the future OS-level sandbox.
- Proving that an agent has not exfiltrated data.
- Automatically merging or deploying changes.
- Provider-specific guarantees about model context, memory, or all tool execution.
- Complete Claude or Codex action coverage: provider-native tool APIs evolve, and NARROW's tested gates do not intercept every possible provider action.
- Separate OS-level containment for Claude subagents or Codex child agents.
- Authentication for a dashboard exposed beyond the local host; the dashboard is intentionally not designed for remote exposure.

## Future hardening gates

Before advertising NARROW as safe for unattended autonomous execution, require:

1. Disposable container/VM runner with explicit mount and network policy.
2. Per-provider adapters with tested permission-mode detection and real authenticated end-to-end sessions.
3. Structured tool-event observation plus provider-native pre-action gates for every supported provider, not filesystem polling alone.
4. Bounded retry budgets, wall-clock budgets, and human escalation rules.
5. Cross-platform process-tree tests and fault injection.
6. Secret-handling review and redaction tests.
7. Independent security review and documented known limitations.


## Concurrent task execution

NARROW serializes agent/drive/verification/finish operations with a per-task OS file lock. This reduces accidental concurrent writers and prevents a second NARROW process from starting against the same task while the first-level agent or check still holds the inherited lease. The local lock file is opened without following symlinks where supported and rejected if it is not a single-link regular file.

Residual risks: the lock does not restrict the agent's OS permissions. A child or detached descendant that intentionally closes or does not inherit the lock handle can outlive the lease. Windows handle inheritance needs dedicated CI and provider testing. After a supervisor crash, users must inspect active processes and the workspace; NARROW does not automatically relaunch or kill orphaned agents.
