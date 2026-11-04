# Threat Model — NARROW v0.5.0 legacy baseline (pre-Beta)

Last reviewed: 2026-11-03

## Scope and security objective

NARROW is a local-first supervisor for AI-assisted repository work. Its security objective is to reduce accidental damage, make observable boundaries explicit, preserve a reviewable record, and refuse unsupported completion claims.

NARROW v0.5.0 is **not** a universal sandbox, endpoint security product, or proof that an agent is benign. It cannot defend a user machine against a malicious process that already runs with the user's permissions. The protection actually available depends on the execution path selected below.

## Protected assets

- The original repository's committed state, active branch, and ordinary visible working-tree changes.
- Existing project files and the user's uncommitted work.
- Credentials, environment values, local data, and private project information.
- Task scope, acceptance criteria, verification evidence, and continuity records.
- The user's authority to review and manually integrate changes.

## Actors and trust assumptions

| Actor or input | Trust assumption |
|---|---|
| User / maintainer | Trusted to choose the task, allowed paths, providers, checks, sandbox configuration, and acceptance evidence. NARROW cannot validate the user's intent. |
| NARROW package and local state | Trusted to implement the documented checks. A compromised interpreter, package, host, or local state invalidates the trust model. |
| AI agent and model output | Untrusted for correctness. It may misunderstand, hallucinate, over-edit, ignore instructions, or produce malicious code. |
| Repository contents | Untrusted instructions and executable code. Build scripts, tests, package managers, Git hooks/filters, editor tasks, and project tooling may have side effects. |
| Verification commands | User-selected executable programs. They can be incorrect, malicious, or have arbitrary effects allowed by the OS. |
| Provider CLI and service | External trust boundary. Account configuration, provider-side data retention, model behavior, tool semantics, and CLI updates are not controlled by NARROW. |
| Same-user local processes | Outside the isolation boundary. A process with the same user permissions may inspect or modify local files and processes. |

## Execution profiles and actual boundaries

| Entry point | Boundary NARROW configures | Important limitation |
|---|---|---|
| `narrow drive --provider claude` | Uses the native Claude Code adapter, required fail-closed Claude shell sandbox settings, a strict empty-by-default shell network allowlist, an empty MCP configuration for this profile, and NARROW PreToolUse path gates for selected direct file/search tools. | Claude's shell sandbox does not contain every in-process provider tool or helper. NARROW's hooks cover a documented subset, not every possible tool. This is not a disposable VM or complete OS boundary. |
| `narrow drive --provider codex` | Uses the native Codex CLI with its `workspace-write` sandbox and non-interactive `never` approval semantics; NARROW adds task-copy monitoring and a completion gate. | The actual boundary is the installed Codex version and its provider-managed sandbox. Do not add permission-bypass flags or assume the semantics of a different version. |
| `narrow drive --agent NAME` | Executes the configured argv command in the task copy; NARROW monitors repository-visible scope and task state. | **NARROW does not configure an OS sandbox for a generic adapter.** The agent process and its descendants retain the invoking user's permissions unless the user independently wraps them in an OS sandbox. |
| `narrow run` / `narrow resume` | Executes the exact command explicitly supplied by the user in the task copy, with scope monitoring and a task execution lease. | These are low-level command wrappers, not the native provider profiles. They do not install the Claude/Codex policies above and do not sandbox an arbitrary command. |
| `narrow verify` or automatic checks inside `drive` | Runs a configured argv array with `shell=False`, explicit confirmation by default, a timeout, bounded output capture, and snapshot checks. | The executable can start other processes, access permitted files/services, and cause external side effects with the user's permissions. `--yes` / `--yes-checks` deliberately skip the interactive confirmation. |
| `narrow dashboard` | Read-only HTTP server bound to `127.0.0.1`, loopback client and Host/Origin checks, local assets, restrictive headers, and sanitized read-only endpoints. | It is unauthenticated and intended only for the same local device. Do not expose it through a tunnel, reverse proxy, or public interface. Local malware, browser extensions, and other same-device users remain relevant. |

**Recommended default:** use `narrow drive --provider claude` or `--provider codex` for the native profile, keep provider approvals enabled, and review every configured check. For untrusted code or repositories, run the whole workflow in a disposable VM/container with restricted mounts, no host credentials, restricted network, resource limits, and a low-privilege account. NARROW does not create or manage that outer sandbox in v0.5.0.

## Threats, controls, and residual risk

| Threat | v0.5.0 control | Residual risk |
|---|---|---|
| Agent edits files outside the task copy | NARROW creates an independent Git copy without hardlinks or configured remotes. Native provider profiles add provider-level sandbox controls. The supervisor checks the original checkout's Git state during and after a run. | User-level processes can still access arbitrary paths unless the selected provider/outer OS sandbox prevents it. Git status monitoring is not a complete system-wide filesystem monitor and does not observe every external side effect. |
| Agent changes files outside its declared scope | Required explicit allowlist, optional denylist, preflight/live/final scans, changed-file limits, symlink/hardlink/special-file checks, selected Claude direct-tool hooks, and fail-closed monitor errors. | Detection is periodic and mostly repository-state based. A write may occur before detection; ignored and unusual filesystem behavior, races, absolute-path writes, external processes, or unobserved tools can bypass visibility. Provider boundaries remain essential. |
| User's pre-existing uncommitted work is overwritten | Task creation refuses a dirty original checkout and executes in a separate Git copy. NARROW does not merge or copy results back. | This is not a backup system. External same-user processes and malicious commands can still affect original files if the OS/provider boundary permits it. |
| Agent claims success without correct work | Configured checks, snapshot-bound results, explicit acceptance evidence, and a separate finish gate. The agent's exit code alone never verifies the task. | Tests may be incomplete, weak, self-modified, or semantically wrong. Human review and domain-specific testing remain necessary. |
| Old test/acceptance evidence is reused after edits | Checks and acceptance evidence are tied to the current snapshot hash; changed work invalidates prior evidence. The finish gate also checks the configured command has not changed. | The guarantees depend on the files and filesystem states the snapshot scanner can enumerate and hash. This is not a proof of semantic equivalence or behavior. |
| Malicious or unsafe test/check command | Argv arrays only, no shell interpolation, explicit command display and user confirmation by default, timeouts, output limits, and change detection. | `shell=False` prevents shell-string interpolation; it does not make the executable safe. The command runs with user permissions and can access networks, credentials, or external paths not otherwise blocked by the OS. |
| Agent or check runs indefinitely | Run timeout, bounded repair attempts, output draining/retention limits, and best-effort process-group cleanup. | Detached/escaped children may survive. Process-tree containment is OS-dependent and not guaranteed on every platform. Provider-side tasks may continue independently. |
| Concurrent NARROW commands write to one task | Non-blocking OS lock per task covers run/drive/verify/finish; first-level agent/check receives the lock handle. | Locks coordinate NARROW operations; they do not prevent other programs from opening the workspace. Descendants can close or fail to inherit the handle. Windows crash-lifetime behavior remains unvalidated. |
| Sensitive task content leaks through local notes/events | Local SQLite state, selected provider metadata instead of raw transcripts, bounded output tails, and common-secret-pattern redaction. No NARROW cloud or mandatory telemetry. | Redaction is heuristic and cannot recognize every secret or personal datum. Provider CLIs may transmit prompts/repository context under their own policies. Avoid putting secrets into task text or evidence. |
| Dashboard exposes operational data remotely | Loopback-only bind, Host/Origin checks, read-only endpoints, local assets, CSP/security headers, and no raw notes/prompts/check output in responses. | No authentication is provided; do not make it remotely reachable. Same-device compromise and browser attacks are out of scope. |
| Repository instructions manipulate the agent | Prompts identify repository content and prior output as untrusted data; task contract and allowed paths are repeatedly supplied; provider sandbox and hooks add boundaries. | Prompt instructions are not a security boundary. Prompt injection can still affect model decisions and tools not covered by the provider/OS controls. |
| Provider or host is compromised | No NARROW cloud dependency; native provider security controls are enabled where supported. | NARROW cannot defend a compromised host, Python runtime, provider executable, provider account, model service, or OS kernel. It cannot prove that data was not exfiltrated. |

## Human review and completion semantics

The human-review path is deliberately explicit: `narrow inspect`, examine the actual isolated-workspace diff and all untracked files, run reviewed checks, record each acceptance criterion with `narrow accept --criterion ... --evidence ...`, then run `narrow finish`. See [HUMAN_REVIEW.md](HUMAN_REVIEW.md).

The acceptance command records the user's assertion; it cannot prove the person actually inspected the change. `VERIFIED_COMPLETE` means the configured checks passed for the current snapshot, acceptance evidence is current, the task remains in scope, the task base/branch match, and the original checkout is clean and unchanged. It does **not** certify semantic correctness, absence of vulnerabilities, completeness of tests, or production readiness.

## Explicit non-goals for the v0.5.0 legacy baseline

- Defending a machine against a malicious local user, compromised OS, or compromised NARROW runtime.
- A universal OS sandbox around all provider tools, hooks, plugins, helper processes, agents, and checks.
- Guaranteed process-tree containment on all platforms, including detached descendants.
- Preventing all absolute-path writes, network access, or data exfiltration outside an OS/provider sandbox.
- Automatically committing, merging, pushing, deploying, publishing, or integrating changes.
- Proving that a model's work or a test suite is correct.
- Providing a remote multi-user dashboard or NARROW-hosted service.

## Validation status and release gates

The checked-in unit suite and CI exercise local behavior and fake provider processes. They are not authenticated live Claude/Codex sessions and do not replace an independent security assessment.

Before describing NARROW as suitable for unattended execution or a stable security-sensitive release, require:

1. Authenticated, maintainer-run end-to-end sessions with supported Claude Code and Codex CLI versions, including resume and provider permission-mode assertions.
2. A maintained disposable container/VM runner with explicit read/write mounts, network policy, no host credentials, process/resource limits, and documented failure behavior.
3. Cross-platform process-tree and lock-lifetime fault-injection tests, including Windows inherited-handle crash behavior and macOS.
4. Adversarial path, symlink/hardlink, ignored-file, process escape, prompt-injection, secret-handling, and dashboard-origin tests.
5. Independent security review and a documented supported-platform/provider matrix.
6. Reproducible package builds and a versioned release process, with artifact integrity/signing and publication permissions configured.

Use the current Beta preview as a task supervisor with manual review—not as an autonomous security boundary. For untrusted repositories or code, use a separately configured disposable OS-level sandbox.
