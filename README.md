# NARROW

![Product checks](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/workflows/tests.yml/badge.svg?branch=main)
![CodeQL](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/workflows/codeql.yml/badge.svg?branch=main)

**AI can start the work. NARROW keeps the workflow on track, verifies the result, and leaves the final decision in human hands.**

NARROW is a local-first workflow supervisor for AI coding agents. It turns an implementation request into a structured task, gives the agent a dedicated Git workspace, records decisions and progress, monitors changes, runs bounded verification and repair, and provides an evidence-backed review process in the local Control Center.

NARROW works with the coding tools developers already use. Project data and the task journal remain on the developer's machine; NARROW does not require a NARROW-hosted account or API key.

## Product capabilities

- **Task contracts:** define the goal, allowed and denied paths, acceptance criteria, and execution limits before work begins.
- **Dedicated Git workspaces:** each task gets its own working copy, keeping ordinary agent edits separate from the original checkout.
- **Native provider support:** use Claude Code, Codex CLI, or a configured command adapter.
- **Continuity across sessions:** preserve task context, handoffs, provider metadata, verification records, and an auditable local event history.
- **Live scope monitoring:** track repository changes and apply provider hooks to selected direct file operations.
- **Bounded verification and repair:** run reviewed checks against the current workspace snapshot and allow a configured number of repair attempts.
- **Deterministic review:** inspect scope, workspace integrity, signed check evidence, sensitive paths, and acceptance freshness with `narrow review`.
- **Human decision controls:** confirm criteria with evidence, request changes, reject a task, or approve and finish after the independent completion gate passes.
- **Local Control Center:** review the task queue, unified diff, verification results, risk findings, and human decision history in a responsive browser interface.
- **Container execution options:** isolate verification commands with Docker/Podman and run Claude or Codex through the optional POSIX/local-Docker agent profile.

## How the workflow works

1. **Set up the project.** NARROW inspects common project manifests and proposes a conservative profile and verification commands.
2. **Define the task.** Create a contract with the requested outcome, allowed paths, denied paths, and acceptance criteria.
3. **Run the agent.** Use a native provider or a configured command adapter in the task workspace.
4. **Monitor and preserve context.** NARROW records changes, decisions, handoffs, and provider events locally.
5. **Verify the result.** Run the configured checks and use bounded repair when the verification policy permits it.
6. **Review the evidence.** Inspect the diff, automated findings, current check results, and acceptance criteria.
7. **Make the human decision.** Request changes, reject, or approve. Approval invokes the independent `narrow finish` gate.

## Install

Requirements: Python 3.11+, Git, and a supported local agent CLI for native provider execution.

Install the current product source:

```bash
git clone https://github.com/ahmedfayedesmail-ui/narrow-ai.git
cd narrow-ai
python -m pip install .
narrow --version
```

For development and tests:

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
```

## One-command project setup

Run from the root of your Git repository:

```bash
narrow setup
narrow setup --apply
narrow security
```

`narrow setup` detects a web, Python, data, or general project profile and presents a read-only onboarding plan. The explicit `--apply` step asks before saving the profile or adding missing verification suggestions. Existing checks are preserved, and discovered commands are not run automatically.

## Start a task

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

Define the checks in `.narrow/config.json` and review every command before approving it. Checks are argv arrays, not shell strings:

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

## Run an agent

Authenticate with the provider CLI directly. NARROW does not collect provider passwords or account tokens.

```bash
# Claude Code
narrow drive --provider claude --effort high --max-attempts 2

# Codex CLI
narrow drive --provider codex --effort high --max-attempts 2

# Configured command adapter
narrow drive --agent my-agent --max-attempts 2
```

Provider model names and supported reasoning levels depend on the selected provider and account.

## Containerized agent execution

The optional `--container-agent` profile runs Claude Code or Codex CLI in a digest-pinned agent image with an execution-scoped internal network and a separately routed egress-proxy sidecar. It is available on POSIX hosts using the local Docker context.

Configure the image, proxy image, and exact provider hosts in `.narrow/config.json`:

```json
{
  "agent_container": {
    "image": "ghcr.io/your-org/narrow-agent@sha256:<64-hex-digest>",
    "proxy_image": "ghcr.io/your-org/narrow-egress-proxy@sha256:<64-hex-digest>",
    "egress_allow_hosts": ["api.openai.com"]
  }
}
```

Then select the provider credential explicitly:

```bash
export OPENAI_API_KEY=...
narrow drive --provider codex --container-agent
```

```bash
export ANTHROPIC_API_KEY=...
narrow drive --provider claude --container-agent
```

Build and pin the images using [the container image guide](examples/containers/README.md). The provider process can read its selected API key inside the container. The Docker daemon and host kernel are part of the trusted computing base; the exact execution boundaries are documented in [SECURITY.md](SECURITY.md) and the [agent runtime architecture](docs/AGENT_SANDBOX_DESIGN.md).

## Review and approve work

Run deterministic review from the terminal:

```bash
narrow review --task <task-id>
narrow review --task <task-id> --json
```

Open the local Control Center:

```bash
narrow dashboard
```

The review panel provides:

- A bounded unified diff and task-scope classification.
- Current signed verification results and stale-evidence detection.
- Security-sensitive path findings for focused review.
- Evidence entry for each acceptance criterion.
- **Request changes**, **Reject**, and **Approve & finish** actions.

The same decisions are available through the CLI:

```bash
narrow review-action --decision accept_criterion --task <task-id> --criterion 1 --evidence "I inspected the current diff and verified this criterion."
narrow review-action --decision request_changes --task <task-id> --reason "Add regression coverage for the timeout path."
narrow review-action --decision reject --task <task-id> --reason "The proposed approach does not meet the requirement."
narrow review-action --decision approve --task <task-id>
```

Approval is available only when the current review packet, signed checks, scope and integrity gates, and human acceptance evidence are ready. The final completion gate is independently rechecked before the task is marked verified complete. Security-sensitive changes also require explicit acknowledgement and a written review note.

## Security and privacy

NARROW is local-first: task contracts, SQLite journal data, review decisions, and evidence are stored in the local project state. Provider requests are handled by the provider CLI and account selected by the developer. Optional local model proposals use a loopback Ollama endpoint.

Execution controls depend on the selected mode. Native Claude and Codex use their provider-specific enforcement; generic command adapters inherit the invoking user's permissions unless independently constrained. The optional Docker agent profile uses a separate network and proxy topology, while Docker and the host kernel remain trusted. Verification sandboxing is configured separately from agent execution. See [the security policy](SECURITY.md), [the threat model](docs/THREAT_MODEL.md), and `narrow security` for the exact profile-specific boundaries.

## Supported environment

| Capability | Environment |
|---|---|
| Core CLI, task management, review, and local Control Center | Python 3.11+ on supported Git platforms |
| Native Claude Code and Codex CLI | Where the provider's own CLI is supported |
| Verification container | Local Docker or Podman, when configured |
| Claude/Codex agent container profile | POSIX host with the local Docker context |
| Third-party adapters | Python entry points in the `narrow.adapters` group |

## Documentation

- [Product profile](docs/PROJECT_PROFILE.md)
- [Product architecture](docs/PRODUCT_ARCHITECTURE.md)
- [Security policy](SECURITY.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Agent runtime architecture](docs/AGENT_SANDBOX_DESIGN.md)
- [Provider plugins](docs/PROVIDER_PLUGINS.md)
- [Container images](examples/containers/README.md)
- [Changelog](CHANGELOG.md)
- [Release notes](docs/RELEASE-NOTES-v1.0.0.md)
- [Package publishing](docs/PUBLISHING.md)

**NARROW 1.0.0** — local-first AI coding workflow supervision with evidence-driven verification and human-controlled completion.
