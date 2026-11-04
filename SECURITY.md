# Security Policy

NARROW 1.0.0 includes task-scoped workflow controls, provider-specific execution protections, signed verification evidence, a deterministic review gate, and an authenticated local Control Center.

## Reporting a vulnerability

Please do not report exploitable vulnerabilities in public issues.

Report security concerns privately through GitHub's private vulnerability reporting feature when available. Include the affected version, impact, reproduction steps, and any suggested mitigation. Do not include credentials, secrets, or personal data.

## Security model

NARROW applies layered controls. The effective boundary depends on the execution mode selected by the developer.

| Execution mode | Controls provided by NARROW | Operating boundary |
|---|---|---|
| Native Claude Code | Required provider shell sandbox settings, selected fail-closed direct-file hooks, task scope monitoring, and local evidence recording | Claude's provider-managed shell boundary and the documented hook coverage apply; not every provider helper or in-process tool is mediated by NARROW |
| Native Codex CLI | Provider-managed `workspace-write` mode, non-interactive approval policy, task scope monitoring, and local evidence recording | Codex's installed CLI and its provider-managed sandbox define the execution boundary |
| Generic `--agent` adapter | Task workspace, execution lease, path monitoring, bounded workflow, and completion gate | The configured process uses the invoking user's OS permissions unless the user independently places it in an OS sandbox |
| Direct `run` / `resume` | Explicit command execution in the task workspace with task locking and workflow monitoring | The supplied process and its descendants use the invoking user's OS permissions unless independently constrained |
| Docker agent profile | Digest-pinned image, non-root container user, execution-scoped internal network, egress-proxy sidecar, task-scoped credential mount, resource limits, supervised lifecycle, and explicit recovery | POSIX host with local Docker context; Docker daemon and host kernel are trusted |
| Verification container | Optional network-disabled container for configured checks, with resource limits and task-workspace mount | Separate from agent execution; configured checks may execute arbitrary project code within the selected container policy |

## Important operating facts

- A task Git workspace separates repository edits from the original checkout. It does not, by itself, restrict filesystem, process, or network permissions on the host.
- A provider API key mounted into an agent container is readable by the provider process and tools running with that container identity. The credential lease limits host-profile exposure; it does not hide the key from the agent.
- The Docker daemon, host kernel, and host services reachable from the Docker bridge are part of the trusted computing base for the container profile.
- Generic command adapters and low-level direct execution do not receive an OS sandbox from NARROW automatically.
- Verification commands are user-selected programs. Review their commands and dependencies before execution. Verification sandboxing is separately configurable.
- Same-user host processes remain outside NARROW's task boundary.
- Human review, current signed verification evidence, and the independent finish gate are required before a task can be marked verified complete.

## Operational safeguards

The local Control Center uses a per-launch token, same-origin checks for review decisions, bounded JSON request bodies, and a per-task execution lock. Review and decision events are recorded locally. The review endpoint does not launch agents or execute arbitrary shell commands.

For the detailed assets, trust assumptions, execution profiles, and threat scenarios, see [the threat model](docs/THREAT_MODEL.md), [the agent runtime architecture](docs/AGENT_SANDBOX_DESIGN.md), and `narrow security --json`.
