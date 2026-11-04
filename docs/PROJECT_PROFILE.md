# NARROW — Project Profile

**Tagline:** AI can start the work. NARROW helps make sure the work is finished safely.

**Category:** Local-first AI coding-agent task supervisor / execution coordination layer.

**Project status:** Public Beta preview — v0.6.0b1 (unreleased source on `main`). Not a stable release and not a universal OS sandbox. The older published v0.5.1 PyPI package predates the current Beta source and is unchanged.

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

- PR #16 and PR #17 are merged into `main`; the Beta source includes discovery, human-reviewed task proposals, opt-in containerized verification, and the opt-in Codex container/proxy runtime.
- The merged-main workflow [#41778743003](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41778743003) passed all nine jobs on commit `979198b`: Ubuntu/Python 3.11–3.14, macOS, Windows repaired/blocked, Docker image build plus real proxy-only egress acceptance, and package build/install.
- CodeQL passed on the same commit in [run #41778742977](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41778742977).
- No authenticated live Codex account session was performed. The project owner has explicitly decided that a live session is not required for the Beta label. No live provider execution is claimed.
- The CI results establish the tested deterministic workflows and Docker egress topology; they are not an independent security audit, universal sandbox assessment, or proof of semantic correctness.
- The older published `v0.5.1` PyPI package predates the current Beta source. Android/Termux is unsupported and unvalidated.

## Installation and demo

```bash
git clone https://github.com/ahmedfayedesmail-ui/narrow-ai.git
cd narrow-ai
git checkout main
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

NARROW is in public Beta preview, not stable. The optional Codex container profile is POSIX/local-Docker only, remains explicitly opt-in, and is not a universal agent sandbox. Its Docker proxy-only egress acceptance job passed on the recorded merged-main CI run. A live authenticated Codex session is not a Beta-label prerequisite by the project owner's decision, and none is claimed. Cross-platform fault-injection, adversarial boundary testing, and independent security review remain open maturity gates before stable release.

The next milestone is evidence-led user testing and closure of these gates—not feature expansion for its own sake.

## Project links

- [Source repository](https://github.com/ahmedfayedesmail-ui/narrow-ai)
- [README and installation](../README.md)
- [Architecture](ARCHITECTURE.md)
- [Demo guide](DEMO.md)
- [Human review guide](HUMAN_REVIEW.md)
- [Security policy](../SECURITY.md)
- [Roadmap](ROADMAP.md)

The experimental POSIX/local-Docker agent profile supports either the native Claude or Codex CLI. Claude's direct file tools use the container-mounted NARROW policy hooks, while provider-managed subagents and MCP remain disabled in this profile. No host provider home or global login directory is mounted. The selected API credential is visible to the running provider process and must be treated as exposed to the agent.