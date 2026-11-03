# NARROW — Project Profile

**Tagline:** AI can start the work. NARROW helps make sure the work is finished safely.

**Category:** Local-first AI coding-agent task supervisor / execution coordination layer.

**Project status:** Public Alpha — v0.5.1. Not a stable release and not an OS sandbox. Feature-branch container-agent work is opt-in and is not included in the published package.

## What it is

NARROW sits between a developer's task and the AI coding agent doing the implementation. It turns a loosely scoped coding request into an explicit task contract, creates a separate Git copy for the work, records task decisions and progress locally, monitors observable repository changes, runs bounded verification and repair cycles, and requires human review before a task can be marked complete.

NARROW is designed to work with locally installed coding-agent CLIs. It does not operate a hosted agent service, require a NARROW API key, or upload project source to a NARROW backend.

## The problem it addresses

AI coding sessions can lose context, exceed the intended scope, stop after describing a change rather than proving it, or repeat unsuccessful repair attempts. A developer still needs to know:

- What exactly was the agent allowed to change?
- What decisions and progress must survive a new session?
- Which checks ran against the current version of the workspace?
- Did a failed attempt lead to another bounded attempt or a review-needed stop?
- Has a person inspected and accepted the actual change?

NARROW makes these controls explicit and leaves a local evidence trail.

## Core capabilities

- **Task contracts:** objective, required allowed paths, optional denied paths, and acceptance criteria.
- **Separate Git workspaces:** a task-specific Git copy without configured remotes; the original checkout is not the normal agent working directory.
- **Provider adapters:** native Claude Code and OpenAI Codex CLI workflows, plus a generic command adapter with a different and weaker security boundary.
- **Task continuity:** local SQLite journal, decisions, notes, progress, provider session/thread identifiers, and compact handoff documents.
- **Scope observation:** preflight, live, and final repository checks with selected provider-native hooks where available.
- **Bounded execution:** explicit attempt and time limits, snapshot-bound verification, and a review-needed outcome when the work cannot be verified.
- **Human acceptance:** criterion-by-criterion evidence and a separate finish gate; an agent's own claim or a green test does not auto-approve work.
- **Local Control Center:** read-only dashboard bound to loopback.

## Intended users

NARROW is intended for software developers and engineering teams who already use AI coding agents and want stronger task structure, continuity, observable progress, bounded retries, and a deliberate review step without moving their code into a NARROW-hosted service.

It is especially suited to focused repository tasks where the developer can define allowed paths and meaningful verification commands.

## Positioning and security boundary

NARROW is a **supervisor and coordination layer**, not a universal sandbox, endpoint-security product, or correctness oracle.

- Native Claude execution requests Claude's fail-closed shell sandbox configuration and uses NARROW hooks for a limited set of direct file/search tools. This does not contain every provider helper or tool.
- Native Codex execution requests Codex's `workspace-write` sandbox and provider approval semantics. Enforcement depends on the installed Codex CLI.
- The generic `--agent` adapter and direct `run` / `resume` commands do not receive an OS sandbox from NARROW.
- Verification executables run with the invoking user's OS permissions. Review every configured command before running it.
- A task's separate Git copy and per-task execution lock are not OS containment. Detached processes, same-user processes, network access, and other external side effects remain outside NARROW's complete control.

For untrusted repositories, generated code, or unattended execution, use a separately configured disposable VM/container with restricted mounts, no host credentials, a restricted network, resource limits, and a low-privilege account. Read [SECURITY.md](../SECURITY.md), the [Safety Model](SAFETY_MODEL.md), and the [Threat Model](THREAT_MODEL.md).

## Current validation

The published `v0.5.1` package keeps the previously validated Alpha runtime and contains no enabled agent-container runtime. Its feature-branch changes are validated separately before any later Beta release.

- Ubuntu with Python 3.11, 3.12, 3.13, and 3.14.
- macOS with Python 3.13.
- Windows Server 2025 repaired and blocked demo workflows, task-lock tests, and CLI smoke checks.
- Wheel and source-distribution build, plus clean wheel installation.

Historical v0.5.0 CI results are evidence for that earlier Alpha source snapshot only. They are not authenticated live Claude/Codex sessions, real agent-container E2E, or an independent security audit. See the [QA record](QA-RESULTS-v0.5.0.md).

## Installation and demo

```bash
git clone https://github.com/ahmedfayedesmail-ui/narrow-ai.git
cd narrow-ai
git checkout v0.5.1
python -m pip install .
narrow --version
```

Run the deterministic local examples from the source checkout:

```bash
PYTHONPATH=src python examples/demo.py --scenario repaired
PYTHONPATH=src python examples/demo.py --scenario blocked
```

The demo uses a fake local agent and deliberately stops for human review; it is not a live provider or sandbox test.

## Maturity and release gates

NARROW remains Alpha. The experimental Codex container profile is POSIX/local-Docker only and is not a supported universal agent sandbox. Before announcing Beta, the branch must pass the real Docker proxy-only network acceptance job and broader runtime tests; an authenticated Codex session, cross-platform fault-injection, adversarial boundary testing, and independent security review remain additional gates.

The next milestone is evidence-led user testing and closure of these gates—not feature expansion for its own sake.

## Project links

- [Source repository](https://github.com/ahmedfayedesmail-ui/narrow-ai)
- [README and installation](../README.md)
- [Architecture](ARCHITECTURE.md)
- [Demo guide](DEMO.md)
- [Human review guide](HUMAN_REVIEW.md)
- [Security policy](../SECURITY.md)
- [Roadmap](ROADMAP.md)
