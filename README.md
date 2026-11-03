# NARROW

**AI can start a task. NARROW helps carry it through to a reviewable finish.**

NARROW is a local-first task continuity and completion supervisor for AI coding agents. It is designed for everyday engineering work where an agent must remember context, stay within an agreed scope, verify its changes, and leave a clear handoff instead of stopping at "done".

NARROW works with the coding tools you already use. It does not require a NARROW API key or send your source code to a NARROW-hosted service. Provider sign-in and model usage remain with the provider's own CLI and account.

> **v0.4.0 Alpha.** This is experimental software, not a security certification or a guarantee of correct changes. Review the safety boundaries below before using it on valuable repositories.

## What it does

- **Narrow task contracts:** record the goal, acceptance criteria, permitted paths, and restricted paths before work begins.
- **Secondhand memory:** preserve decisions, progress, verification evidence, and provider session identifiers in a local SQLite journal. Generate a compact handoff for a fresh session or a different provider.
- **Keep working to a bounded finish:** run an agent, inspect the resulting diff, execute explicitly approved checks, and permit only a configured number of repair attempts.
- **Evidence, not self-attestation:** verification is tied to a workspace snapshot. Later changes make old checks stale. Human acceptance remains a separate gate.
- **Native agent adapters:** use Claude Code or OpenAI Codex CLI, with a generic command adapter also available.
- **A local Control Center:** inspect task state, scope summaries, acceptance progress, check freshness, and a privacy-minimized activity timeline from a read-only loopback dashboard.
- **Task portfolio and model controls:** list active tasks and choose provider reasoning effort where the selected model/account supports it.

## v0.4.0 release

The complete source, tests, documentation, threat model, safety model, provider notes, and GitHub templates are included in the release archive:

- [Download NARROW v0.4.0 source archive](./narrow-v0.4.0.tar.gz)
- [Verify the archive SHA-256](./narrow-v0.4.0.tar.gz.sha256)
- [Read the changelog](./CHANGELOG.md)

## Install

Requirements: Python 3.11 or newer, Git, and a supported local agent CLI for native provider execution.

Download and extract the source archive, then run:

```bash
tar -xzf narrow-v0.4.0.tar.gz
cd NARROW
python -m pip install .
narrow --version
```

For development and tests:

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
```

## Everyday workflow

Run these commands from the original project checkout:

```bash
narrow init
narrow start \
  --title "Improve checkout reliability" \
  --goal "Fix the retry behavior without changing the public API." \
  --allow "src/checkout/**" \
  --allow "tests/checkout/**" \
  --deny "src/checkout/secrets/**" \
  --accept "A retryable timeout is retried at most twice." \
  --accept "The public API remains unchanged."
```

Review `.narrow/config.json` and add the exact verification commands you approve. Checks are argv arrays, not shell strings. Then run a provider:

```bash
narrow providers --versions
narrow doctor --versions

# Claude Code
narrow drive --provider claude --effort xhigh --max-attempts 2

# OpenAI Codex CLI
narrow drive --provider codex --effort high --max-attempts 2
```

To inspect, continue, and finish work:

```bash
narrow tasks
narrow status --task <task-id>
narrow handoff --task <task-id>
narrow resume --task <task-id>
narrow inspect --task <task-id>
narrow verify --task <task-id> --check <check-name>
narrow report --task <task-id> --format markdown --output work-receipt.md
```

Completion requires current passing checks, a safe workspace, human-confirmed acceptance criteria, and an unchanged original checkout. NARROW does not treat an agent's message or a green test by itself as proof of semantic correctness.

## Local Control Center

Start the local, read-only dashboard:

```bash
narrow dashboard
```

Open `http://127.0.0.1:8765`. The dashboard binds to loopback, uses local assets, and exposes sanitized read-only summaries. It does not return raw notes, prompts, tool arguments/results, or raw check logs. Stop the server with `Ctrl+C`.

## Safety boundaries — read before use

NARROW is a supervisor, not an operating-system sandbox.

- Each task runs in a separate Git copy with remotes removed; normal agent editing does not take place in the original checkout.
- **Agent and check processes still inherit the invoking user's OS permissions.** An isolated Git copy does not prevent access to other files, processes, services, or network resources.
- Claude's shell sandbox and Codex's workspace-write sandbox are provider controls. NARROW adds selected direct-file policy hooks for Claude; it is not a universal filesystem or side-effect monitor.
- Configured verification commands are executable programs. They can have side effects and run with the user's permissions. Review every command before approving it.
- Claude subagents are disabled by default. When explicitly enabled, they share the task workspace and user-level permissions; they do not get separate containment.
- NARROW does not automatically commit, merge, push, publish, deploy, or delete work.
- Never run unattended with untrusted agents, repositories, prompts, hooks, plugins, or checks. For stronger isolation use a disposable container or VM with restricted mounts, credentials, network access, resources, and process permissions.

Read `SECURITY.md`, `docs/THREAT_MODEL.md`, and `docs/SAFETY_MODEL.md` in the source archive before using NARROW on valuable work.

## Validation status

The v0.4.0 local QA suite reports **79 passing tests** on Linux with Python 3.13 and Git 2.47. GitHub Actions is configured to validate Python 3.11–3.14 on Ubuntu. The CI matrix is not evidence that every combination or platform has been independently validated.

Authenticated end-to-end Claude/Codex sessions, macOS, Windows-native execution, Android/Termux, and independent OS-sandbox escape testing remain outstanding. See `docs/QA-RESULTS-v0.4.0.md` for the exact coverage and limitations.

## Contributing and license

Read `CONTRIBUTING.md` and the security policy before opening a change. Safety regressions, privacy leaks, unbounded execution, and misleading security claims are release blockers.

NARROW is distributed under the MIT License; see [LICENSE](./LICENSE).
