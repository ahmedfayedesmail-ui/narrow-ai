# NARROW

**AI can start the work. NARROW helps make sure the work is finished safely.**

NARROW is a local-first task continuity and completion supervisor for AI coding agents. It combines explicit task contracts, isolated Git copies, provider-native execution, compact cross-session memory, live scope monitoring, bounded repair cycles, evidence-based verification, and a local Control Center.

It works with the coding agents you already have. NARROW itself does not require an API key or upload source code to a NARROW service. Provider authentication and usage remain with the provider's own CLI and account.

> **Alpha software.** NARROW is a supervisor and evidence recorder, not a guarantee that an agent is safe or that a change is correct. Read [SECURITY.md](SECURITY.md) and [the threat model](docs/THREAT_MODEL.md) before using it on valuable repositories.

## What's new in v0.5.0

- **Single-flight task execution:** NARROW rejects a second agent, repair loop, verification, or finish operation while the same task already has an execution lease.
- **Inherited lease handle:** the first-level agent/check receives the lock handle. On POSIX, the lease remains held if NARROW exits unexpectedly while that child remains alive.
- **Public source tree:** implementation and tests are checked in alongside the downloadable archive.

This is coordination, not a machine sandbox. Detached descendants may outlive their parent process, and the Windows crash-lifetime path still needs dedicated validation. See [the v0.5 release notes](docs/RELEASE-NOTES-v0.5.0.md).

## What's new in v0.4.0

- **Local Control Center:** responsive, read-only dashboard at `127.0.0.1`, with task state, scope summary, acceptance progress, current/stale checks, and a sanitized activity timeline.
- **Task portfolio:** `narrow tasks` lists active work; `narrow tasks --all --json` emits a bounded, sanitized task index.
- **Model effort controls:** `--effort low|medium|high|xhigh` for Claude Code and Codex CLI, plus Claude-only `max` where the selected model/account supports it. Provider model support remains authoritative.
- **Richer Claude telemetry:** optional Task/Agent tools, task-created/completed and subagent lifecycle hooks, and tool-failure metadata. Subagents require explicit `--allow-subagents` opt-in.
- **Compatibility diagnostics:** opt-in provider version probing reports when Claude Code should be upgraded for full task/subagent telemetry.

## Core workflow

1. **Define the task.** State the objective, allowed paths, denied paths, and acceptance criteria.
2. **Create an isolated Git copy.** The original checkout remains untouched by normal agent editing.
3. **Run an agent.** Use Claude Code, Codex CLI, or a configured generic command adapter.
4. **Watch scope.** NARROW records file changes and uses provider hooks where available to block selected out-of-scope direct file operations.
5. **Preserve continuity.** Decisions, progress notes, failures, verification results, and provider session IDs remain in a local SQLite journal. A compact handoff can resume work in a fresh session or another provider.
6. **Verify and repair.** Explicitly configured checks run under a bounded repair budget. A failure can trigger a limited repair attempt; repeated failure becomes a blocked/review state.
7. **Require human acceptance.** NARROW does not equate an agent saying “done” or a green test with semantic correctness. A human reviews the diff and confirms acceptance criteria before `finish` can mark the task verified.

## Install

Requirements: Python 3.11+, Git, and a supported local agent CLI for native provider execution.

Clone the public source checkout, then install it locally:

```bash
git clone https://github.com/ahmedfayedesmail-ui/narrow-ai.git
cd narrow-ai
python -m pip install .
narrow --version
```

The v0.5.0 Alpha is currently distributed as source: clone this repository or use GitHub's **Code → Download ZIP** for the exact branch snapshot. There is no PyPI package or tagged GitHub Release yet.

For development and tests:

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
```


## Start a task

Run these commands from the **original project checkout**:

```bash
narrow init
narrow start \
  --title "Improve checkout reliability" \
  --goal "Fix the retry behavior without changing the public API." \
  --allow "src/checkout/**" \
  --allow "tests/checkout/**" \
  --deny "src/checkout/secrets/**" \
  --accept "A retryable timeout is retried at most twice." \
  --accept "The public API and successful checkout behavior remain unchanged."
```

Edit `.narrow/config.json` and define at least one reviewed verification command. Checks are argv arrays, not shell strings. Example:

```json
{
  "checks": {
    "unit": {
      "command": ["python", "-m", "pytest", "tests/checkout", "-q"],
      "timeout_seconds": 300
    }
  },
  "max_repair_attempts": 2
}
```

**Review every configured command before approving it.** Verification commands run with your user permissions inside the task copy. They are not sandboxed by NARROW.

## Run a native agent

First, install and authenticate the provider CLI yourself. NARROW never asks for provider credentials.

```bash
narrow providers --versions
narrow doctor --versions

# Claude Code (use a reasoning effort supported by the selected model)
narrow drive --provider claude --effort xhigh --max-attempts 2

# Codex CLI
narrow drive --provider codex --effort high --max-attempts 2
```

To explicitly enable Claude Agent/task tools and subagents:

```bash
narrow drive --provider claude --allow-subagents --effort xhigh

# Higher-autonomy Claude dynamic workflows (requires subagents opt-in)
narrow drive --provider claude --allow-subagents --ultracode
```

Subagents share the task workspace and the same user-level process permissions. This option is off by default. It does not create a separate sandbox for each subagent. `--ultracode` is a separate opt-in to Claude Code's dynamic multi-agent workflow mode; it requires `--allow-subagents`, uses the provider's ultracode mode, and cannot be combined with `--effort`. Model/account support remains provider-controlled. Claude effort levels are model/account dependent; Codex reasoning effort is also model dependent. Omit `--effort` to use the provider's own default.

`--max-budget-usd` is a Claude CLI client-side estimate/cap, not a billing guarantee. Codex has no NARROW-compatible per-run USD cap in this release; use the provider's own usage controls.

## Continue work across sessions

```bash
narrow tasks
narrow status --task <task-id>
narrow handoff --task <task-id>
narrow resume --task <task-id>
```

A handoff is generated from recorded task state, decisions, progress, changes, and evidence. It is deliberately not a full transcript. Only information explicitly recorded in NARROW can be reliably carried forward.

## Inspect, accept, and finish

```bash
narrow inspect --task <task-id>
narrow note --task <task-id> "The failing integration test is caused by the stale token fixture."
narrow decision --task <task-id> "Keep the public response schema unchanged."
narrow verify --task <task-id> --check unit
narrow accept --task <task-id> --criterion 1 --evidence "Reviewed retry test output and implementation diff."
narrow report --task <task-id> --format markdown --output work-receipt.md
narrow finish --task <task-id>
```

A check result is snapshot-bound. Any subsequent workspace change makes the earlier result stale. Completion requires current passing configured checks, human-confirmed acceptance criteria, a safe workspace, and an unchanged original checkout.

The exact review procedure matters: [Human review and acceptance](docs/HUMAN_REVIEW.md) explains how to inspect the isolated diff **and every untracked file**, record criterion-by-criterion evidence, and complete the final gate. NARROW records the user's assertion; it cannot prove that a human actually read the code.

## Reproducible end-to-end demo

The [local demo](docs/DEMO.md) exercises the actual CLI in a disposable temporary Git repository. It shows a failing check followed by a bounded repair/handoff, and a separate scenario that stops at `NEEDS_REVIEW`. It uses a deterministic fake agent—not Claude/Codex—and deliberately leaves the final human acceptance step to the user.

```bash
PYTHONPATH=src python examples/demo.py --scenario repaired
PYTHONPATH=src python examples/demo.py --scenario blocked
```

## Local Control Center

Start the read-only dashboard from the original repository:

```bash
narrow dashboard
```

Open `http://127.0.0.1:8765`. It binds to loopback only, has no remote assets or telemetry, exposes read-only JSON endpoints, and does not return raw notes, prompts, tool outputs, or check logs. It refreshes automatically. Stop it with `Ctrl+C`.

## Supported providers

| Capability | Claude Code | OpenAI Codex CLI |
|---|---|---|
| Native command adapter | Yes | Yes |
| Structured events | `stream-json` | `--json` JSONL |
| Session continuity | Resume session ID | Resume thread ID |
| Reasoning effort | `--effort` | `--config model_reasoning_effort=...` |
| Approval policy | Deny permission prompts in print mode | Non-interactive `--ask-for-approval never` |
| Execution boundary | Required fail-closed shell sandbox plus NARROW direct-file hooks | Provider `workspace-write` sandbox |
| Native subagent support in NARROW | Explicit opt-in | Not enabled by NARROW |

The adapters use documented CLI interfaces, not private transcript or database formats. Actual authenticated end-to-end behavior still needs validation against the exact provider CLI and model installed on the user's machine. See [provider details](docs/PROVIDERS.md).

## Which execution mode should I use?

| Invocation | NARROW-configured boundary |
|---|---|
| `narrow drive --provider claude` | Native Claude profile: required fail-closed shell sandbox plus selected direct-file hooks. The hooks do not cover every provider tool/helper. |
| `narrow drive --provider codex` | Native Codex `workspace-write` sandbox and provider approval semantics. |
| `narrow drive --agent NAME` | Generic argv command only. NARROW does **not** create an OS sandbox for it. |
| `narrow run` / `narrow resume` | Direct user-supplied command wrapper. Does not configure the native Claude/Codex profile. |
| `narrow verify` / checks in `drive` | User-configured executable, shown for confirmation by default, still runs with the user's OS permissions. |

For native provider safeguards, use `narrow drive --provider ...`; do not assume `narrow run -- claude` is equivalent. See the complete [threat model](docs/THREAT_MODEL.md).

## Safety boundaries — read before use

- Each task is a separate Git copy without remotes. The source checkout is not edited by normal NARROW agent execution.
- **A Git copy is not an operating-system sandbox.** Processes launched by NARROW generally inherit the invoking user's OS permissions.
- Claude's required sandbox applies to its shell commands and children; Claude documents that some other tool/helper activity runs outside this shell boundary. NARROW adds direct-file hooks for a limited documented tool set, not a universal filesystem monitor.
- Codex uses its own workspace-write sandbox. NARROW does not bypass provider approvals or sandbox controls.
- Configured verification commands are executable programs. They may have side effects and run with the user's permissions. Review them before approval.
- Never use unattended mode on untrusted repositories, agents, prompts, hooks, plugins, or checks. Use a disposable container/VM with restricted mounts, network, credentials, resources, and process permissions for stronger isolation.
- NARROW does not automatically commit, merge, push, publish, deploy, delete, or integrate changes.
- A model's output, test success, or generated report is not independent proof of correctness. A human must review the actual changes.

See [the safety model](docs/SAFETY_MODEL.md), [the threat model](docs/THREAT_MODEL.md), [the security policy](SECURITY.md), and [the human-review procedure](docs/HUMAN_REVIEW.md).

## Current validation

- The local unit suite covers the CLI, task creation, path/snapshot gates, provider adapters with fakes, dashboard, bounded repair/handoff, and task locks.
- GitHub Actions run [#41644004390](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41642840892) passed on the reviewed PR snapshot: Ubuntu/Python 3.11–3.14 full suites and demos, macOS/Python 3.13 full suite and demos, Windows Server 2025/Python 3.13 lock tests and demos, plus wheel/sdist build and clean wheel installation.
- The run also created, extracted, and tested a source archive from the exact checked-out commit; the tested archive is attached to the workflow run as an artifact. GitHub's Download ZIP is the current-source download route.
- The deterministic demo exercises successful repair and bounded failure with a fake agent. It is not an authenticated provider test.
- Authenticated Claude/Codex end-to-end sessions, provider-version support guarantees, Windows inherited-handle crash lifetime, full OS-sandbox escape testing, and independent security review remain open gates.
- Android/Termux is not a supported or validated platform claim.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md). Safety regressions, privacy leakage, and unbounded execution are release blockers.

## License

MIT — see [LICENSE](LICENSE).
