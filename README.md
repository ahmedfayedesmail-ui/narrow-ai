# NARROW

![CI](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/workflows/tests.yml/badge.svg?branch=main)

**AI can start the work. NARROW helps make sure the work is finished safely.**

NARROW is a local-first task continuity and completion supervisor for AI coding agents. See the concise [project profile](docs/PROJECT_PROFILE.md) for its purpose, intended users, operating model, and maturity. It combines explicit task contracts, isolated Git copies, provider-native execution, compact cross-session memory, live scope monitoring, bounded repair cycles, evidence-based verification, and a local Control Center.

It works with the coding agents you already have. NARROW itself does not require an API key or upload source code to a NARROW service. Provider authentication and usage remain with the provider's own CLI and account.

> **Beta preview — v0.6.0b1 (unreleased source snapshot).** NARROW is a task supervisor and evidence recorder, not a guarantee that an agent is safe or a change is correct. It is not a universal OS sandbox or a stable release. The older published v0.5.1 PyPI package predates the current Beta source and does not include these changes. Read [SECURITY.md](SECURITY.md) and [the threat model](docs/THREAT_MODEL.md) before using it on valuable repositories.

## Beta preview highlights

- Project discovery and test-command proposals are bounded and read-only; discovered checks are not executed automatically.
- Plain-language task proposals require human review before task creation or configuration changes.
- Optional Docker/Podman verification is network-disabled by default and resource-limited.
- An opt-in POSIX/local-Docker Claude/Codex container runtime uses a separate proxy sidecar, execution-scoped networks, digest-pinned images, mounted short-lived credentials, and supervised cleanup.

The Claude/Codex container profile remains experimental and opt-in. Its real Docker proxy-only egress acceptance job passed on merged-main CI run [#41779875499](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41779875499). A live authenticated Codex account run is not required for the Beta label by the project owner's decision, and no live run is claimed.

## What's new in v0.5.0

- **Single-flight task execution:** NARROW rejects a second agent, repair loop, verification, or finish operation while the same task already has an execution lease.
- **Inherited lease handle:** the first-level agent/check receives the lock handle. On POSIX, the lease remains held if NARROW exits unexpectedly while that child remains alive.
- **Public source tree:** source, tests, and documentation are checked in at the repository root; CI builds and tests an exact-commit source archive for each validated release candidate.

This is coordination, not a universal machine sandbox. Windows supervised processes are created inside a kill-on-close Job Object on supported Windows versions, but forced-termination and deep-descendant behavior still needs dedicated Windows validation. See [the v0.5 release notes](docs/RELEASE-NOTES-v0.5.0.md).

## What's new in v0.4.0

- **Local Control Center:** responsive, read-only dashboard at `127.0.0.1`, with task state, scope summary, acceptance progress, current/stale checks, and a sanitized activity timeline.
- **Task portfolio:** `narrow tasks` lists active work; `narrow tasks --all --json` emits a bounded, sanitized task index.
- **Model effort controls:** `--effort low|medium|high|xhigh` for Claude Code and Codex CLI, plus Claude-only `max` where the selected model/account supports it. Provider model support remains authoritative.
- **Richer Claude telemetry:** optional Task/Agent tools, task-created/completed and subagent lifecycle hooks, and tool-failure metadata. Subagents require explicit `--allow-subagents` opt-in.
- **Compatibility diagnostics:** opt-in provider version probing reports when Claude Code should be upgraded for full task/subagent telemetry.

## Beta preview capabilities on main

The current Beta preview on `main` includes four opt-in capabilities added after the older published v0.5.1 pre-Beta package:

- Project discovery: narrow discover inspects common project manifests and suggests test commands. It never runs discovered commands. Use narrow discover --apply to review and add new suggestions to .narrow/config.json.
- Task proposals: narrow propose "Fix the login timeout" drafts a contract without creating a task. Add --ollama to use a model already installed in local Ollama. The model request goes only to 127.0.0.1 and may include a small, bounded selection of source files; it is optional. Every proposed path and criterion requires human review.
- Containerized verification: setting verification_sandbox.mode to auto, docker, or podman wraps configured checks in a local container with no network, read-only container root, dropped Linux capabilities, no-new-privileges, a PID limit, memory/CPU limits, and only the isolated task workspace mounted. The image must already exist locally; NARROW does not pull images. The default remains off for backward compatibility.
- **Experimental Codex agent container:** `narrow drive --provider codex --container-agent` uses a digest-pinned agent image and a separate digest-pinned proxy image. The agent joins only an execution-scoped Docker internal network; the CONNECT proxy is dual-homed to that internal network and a separate execution-scoped uplink. No proxy host port is published. The sidecar's labels, network attachments, read-only token mount, no-publish setting, UID/GID, resource limits, and readiness are inspected before the agent starts. This mode is POSIX/local-Docker only and requires an explicit `OPENAI_API_KEY` in the host environment.

Build and pin the agent/proxy images using the recipes and exact requirements in [examples/containers/README.md](examples/containers/README.md). The Docker daemon and host kernel remain trusted infrastructure; this is not equivalent to a Firecracker microVM or independent security review.

To opt in to the experimental **Claude or Codex agent** container mode, add this reviewed configuration to `.narrow/config.json` (replace both digest references with images you built and audited):

```json
{
  "agent_container": {
    "image": "ghcr.io/your-org/narrow-agent@sha256:<64-hex-digest>",
    "proxy_image": "ghcr.io/your-org/narrow-egress-proxy@sha256:<64-hex-digest>",
    "egress_allow_hosts": ["api.openai.com"]
  }
}
```

The shared agent image contains the version-pinned native Codex and Claude CLIs, Python, and NARROW's in-container manifest hooks. The proxy image contains the matching `narrow.egress_proxy` package. The provider API key must be explicitly present in the host process environment. Run one of:

```bash
export OPENAI_API_KEY=...
narrow drive --provider codex --container-agent
```

```bash
export ANTHROPIC_API_KEY=...
narrow drive --provider claude --container-agent
```

For Claude, task-specific container settings route direct file-tool checks through the mounted, read-only NARROW policy manifest. Claude's provider-managed subagent/Task tools and MCP are deliberately disabled in this container profile; shell commands and their child processes remain inside the outer Docker boundary. For Codex, the CLI keeps its native workspace-write sandbox. In both cases, only the selected provider's explicit API key is passed to the container, not the host provider login/profile directories.

Use the exact provider endpoints required by your account in `egress_allow_hosts` (`api.anthropic.com` for Claude and the OpenAI API hostname for Codex). The mounted key is readable by the provider process. The Docker daemon and host kernel remain trusted, and the agent may still attempt to reach host services exposed on its local bridge. This is not a microVM, a universal boundary against a malicious host process, or an independent security audit. The feature remains opt-in, experimental, and POSIX/local-Docker only. The Docker E2E workflow validates the proxy-only network topology and image/CLI smoke tests; it does not sign in to either provider or claim a live authenticated agent session.
Third-party provider integrations can be installed through the narrow.adapters Python entry-point group. This is an extension mechanism, not built-in LangChain, CrewAI, or AutoGen support. See docs/PROVIDER_PLUGINS.md, docs/SANDBOXING.md, and docs/AGENT_SANDBOX_DESIGN.md for installation, configuration, and the remaining agent-container acceptance gates.

The dashboard remains read-only, and its API now requires a random per-launch token carried in the URL fragment and sent in a custom request header; Host and Origin checks remain in force. Mutation controls are still disabled. On Windows 10 / Windows Server 2016 and newer, supervised children are created suspended inside a kill-on-close Job Object through `PROC_THREAD_ATTRIBUTE_JOB_LIST`; NARROW verifies Job membership before resuming the initial thread. There is no post-creation assignment window or suspended child outside the Job. Unsupported or incompatible Job setups fail closed. Broker-created processes and daemon-managed containers are not guaranteed to be covered, and hard-kill/deep-descendant Windows tests still need a real Windows run. MicroVM support, turnkey framework-specific plugins, a default sandbox around the AI agent itself, and interactive dashboard authorization remain open engineering gates. The Beta preview is available from the `main` source branch. The older published v0.5.1 PyPI package predates the current Beta source and does not include these changes.

## Try the Beta preview workflow (main source branch)

From an initialized project, `narrow discover` reads common project manifests and prints likely test commands without running them. Review and add missing suggestions with `narrow discover --apply`.

To draft a task contract from a plain-language goal and review it before task creation:

```bash
narrow start --goal "Fix the login timeout" --auto-contract
```

For an optional local Ollama proposal (only if Ollama and the chosen model are already installed):

```bash
narrow start --goal "Fix the login timeout" --auto-contract --ollama
```

The contract proposal does not silently approve scope. NARROW prints the proposed paths and acceptance criteria and requires an explicit confirmation. Use `narrow propose "..." --json` to inspect a proposal without creating a task. After task creation, use `narrow contract-history --task <id>` to compare the generated proposal with the human-approved contract.

## Core workflow

1. **Define the task.** State the objective, allowed paths, denied paths, and acceptance criteria.
2. **Create an isolated Git copy.** The original checkout remains untouched by normal agent editing.
3. **Run an agent.** Use Claude Code, Codex CLI, or a configured generic command adapter.
4. **Watch scope.** NARROW records file changes and uses provider hooks where available to block selected out-of-scope direct file operations.
5. **Preserve continuity.** Decisions, progress notes, failures, verification results, and provider session IDs remain in a local SQLite journal. A compact handoff can resume work in a fresh session or another provider.
6. **Verify and repair.** Explicitly configured checks run under a bounded repair budget. A failure can trigger a limited repair attempt; repeated failure becomes a blocked/review state.
7. **Require human acceptance.** NARROW does not equate an agent saying “done” or a green test with semantic correctness. A human reviews the diff and confirms acceptance criteria before `finish` can mark the task verified.

## Deterministic review gate

Run `narrow review` after an agent finishes its bounded work cycle. The command builds a read-only evidence packet and supports `--json` for automation. The same report is available in the local Control Center under **Automated review**.

The deterministic reviewer checks task-contract integrity, unsafe workspace links and hard links, allowed/denied path scope, change-count budget, tracked-diff whitespace hygiene, original-checkout invariants, task branch/HEAD invariants, signed verification evidence bound to the current snapshot and configured command, and human acceptance freshness. It flags credential-like paths as blockers without displaying or parsing their contents and highlights security/deployment/workflow/authentication changes for focused scrutiny.

Decisions mean: `BLOCKED` requires fixing a safety invariant; `NEEDS_VERIFICATION` requires fresh passing checks; `HUMAN_REVIEW_REQUIRED` means machine gates are clear but human acceptance is incomplete; `READY_FOR_FINISH_GATE` means run `narrow finish` for an independent final recheck. A report never approves a change, executes a test, modifies task state, merges, commits, or deploys. It is deterministic evidence triage, not an LLM correctness judge or proof of semantic correctness.

## Diff review and human review queue

The local Control Center now shows tasks waiting for review and a bounded unified diff preview for the selected task. It labels changes against the task allow/deny contract and withholds previews when unsafe workspace links or hard-linked files are detected. The preview is read-only and truncates at 96 KiB; it never executes code or sends diffs to a remote service.

Human decisions remain explicit in the CLI: use `narrow accept --criterion N --evidence "..."` to confirm each acceptance criterion, then `narrow finish` to re-check current-snapshot verification, scope, and original-checkout invariants. The dashboard deliberately has no approve/reject mutation endpoint; viewing a diff is not consent to execute, merge, commit, or complete work.

## One-command project setup

From the root of a Git repository:

```bash
narrow setup
narrow setup --apply
narrow security
```

`narrow setup` detects a web, Python, data, or general profile and prints a read-only plan. The explicit `--apply` step asks before saving the profile and adding only missing verification suggestions; it never runs those commands. Existing checks are not overwritten. The profile gives future `narrow start --auto-contract` calls a conservative starting allowlist.

`narrow security` explains which guarantees come from workflow orchestration and which require a real execution boundary. Profiles do not enable Docker, restrict agent permissions, or turn a Git copy into a sandbox.

## Install

Requirements: Python 3.11+, Git, and a supported local agent CLI for native provider execution.

Clone the public source checkout, then install it locally:

```bash
git clone https://github.com/ahmedfayedesmail-ui/narrow-ai.git
cd narrow-ai
python -m pip install .
narrow --version
```

The current GitHub `main` branch is the unreleased `0.6.0b1` Beta preview. The older published `v0.5.1` PyPI package predates the current Beta source and is unchanged. See [the Beta preview release notes](docs/RELEASE-NOTES-v0.6.0b1.md), [the v0.5.1 release notes](docs/RELEASE-NOTES-v0.5.1.md), and [the Python publishing guide](docs/PUBLISHING.md).

For a reproducible source install:

```bash
git clone https://github.com/ahmedfayedesmail-ui/narrow-ai.git
cd narrow-ai
git checkout main
python -m pip install .
narrow --version
```

After the package has been published to the index you use, install it with `python -m pip install narrow-ai`.

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

- PR #16 and PR #17 are merged into `main`. The Beta source includes discovery, human-reviewed task proposals, optional containerized verification, and the opt-in Claude/Codex container/proxy runtime.
- The complete merged-main workflow [#41778743003](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41778743003) passed all nine jobs on commit `979198b`: Ubuntu/Python 3.11–3.14 unit tests, demos and CLI checks; macOS full suite and demos; Windows repaired/blocked demo and task-lock jobs; Docker image build and real proxy-only egress acceptance; and wheel/source-distribution build plus clean wheel installation.
- CodeQL also passed on the same commit in [run #41778742977](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41778742977).
- No authenticated live Codex account session was performed. The project owner has explicitly decided that such a session is not required for the Beta label; no live provider execution is claimed.
- CI and deterministic fake-agent results are not an independent security audit, universal sandbox assessment, or proof of semantic correctness.
- The older published `v0.5.1` PyPI package predates the current Beta source. Android/Termux remains unsupported and unvalidated.
## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md). Safety regressions, privacy leakage, and unbounded execution are release blockers.

## License

MIT — see [LICENSE](LICENSE).
