# NARROW 1.0.0 — Security Threat Model

Last reviewed: 2026-11-04

## Security objective

NARROW reduces accidental damage during AI-assisted software work, makes task boundaries observable, preserves local evidence, and prevents unsupported completion claims. It combines workflow-level controls with provider-specific and optional container controls.

NARROW is not an endpoint-security product. The effective OS boundary depends on the selected execution profile and the host environment.

## Protected assets

- The original repository's committed state, active branch, and developer working-tree changes.
- Project files and data outside the task workspace.
- Credentials, environment values, local configuration, and private project information.
- Task goals, allowed/denied paths, acceptance criteria, and verification evidence.
- The integrity of the local event journal and human decision record.
- The user's authority to review and approve changes.

## Actors and trust assumptions

| Actor or input | Trust assumption |
|---|---|
| User / maintainer | Chooses the task, scope, provider, checks, container images, and acceptance evidence |
| NARROW package and local state | Trusted to implement the documented orchestration and evidence checks |
| AI agent and model output | Untrusted for correctness; may misunderstand requirements or produce unsafe code |
| Repository contents | Untrusted executable code and instructions; build scripts and tests can have side effects |
| Verification commands | User-selected programs with the permissions of the selected execution profile |
| Provider CLI and service | External system; account configuration, model behavior, tool semantics, and service policies are provider-controlled |
| Docker daemon and host kernel | Trusted infrastructure for the Docker agent profile |
| Same-user host processes | Outside the isolation boundary; may inspect or modify resources accessible to the user |

## Execution profiles

| Entry point | Controls | Boundary |
|---|---|---|
| `narrow drive --provider claude` | Claude Code shell sandbox configuration, selected direct-tool hooks, scope monitoring, bounded workflow | Provider shell sandbox plus documented hook coverage |
| `narrow drive --provider codex` | Codex CLI `workspace-write`, non-interactive approval settings, scope monitoring | Provider-managed Codex sandbox |
| `narrow drive --agent NAME` | Dedicated task workspace, task execution lease, scope monitoring, review and finish gates | Invoking user's OS permissions unless independently wrapped |
| `narrow run` / `narrow resume` | Explicit user-supplied argv, task lock, workflow monitoring | Invoking user's OS permissions unless independently wrapped |
| `narrow drive --provider claude|codex --container-agent` | Digest-pinned image, internal agent network, egress proxy, scoped credential lease, resource limits, host supervision and cleanup | POSIX/local-Docker; daemon and kernel trusted |
| Configured verification | Optional Docker/Podman wrapper, disabled network, read-only container root, resource bounds | Verification commands only; separate from agent execution |

## Main threats and mitigations

| Threat | NARROW controls | Residual risk |
|---|---|---|
| Agent modifies unrelated repository files | Dedicated task workspace, allow/deny path monitoring, direct-file hooks where supported | Host-level process permissions depend on provider or container mode |
| Agent reports completion without correct work | Snapshot-bound signed checks, acceptance evidence, deterministic review, independent finish gate | Tests may be incomplete or semantically weak; human domain review remains necessary |
| Stale verification is mistaken for current evidence | Check signatures include the workspace snapshot and configured command | A compromised host or local signing key invalidates the guarantee |
| Repair loop repeats unsafe work | Bounded attempts and deterministic review before each continuation | A novel risk not covered by the review policy requires human judgment |
| Credential-like files enter the change set | Sensitive-path detection blocks review without reading file contents | Path heuristics cannot identify every possible secret embedded in ordinary source files |
| Container reaches unapproved internet hosts | Agent internal network plus a separately routed allowlisting proxy | Docker daemon, host kernel, and host bridge services remain trusted |
| Provider key is exposed to the agent | Task-scoped credential lease and selected-key-only mount | The running provider process and tools can read the key |
| Unauthorized dashboard decision | Per-launch token, same-origin validation, bounded JSON, task execution lock | Local host compromise or token theft is outside the dashboard boundary |

## Data handling

Task contracts, SQLite journal records, review results, and verification metadata are stored in local project state. NARROW does not require a hosted NARROW account. Provider traffic is handled by the provider CLI and account configured by the developer. Optional local proposal generation can use a loopback Ollama service.

## Recommended operating practice

Use the narrowest task scope that fits the work, review verification commands, prefer short-lived provider credentials, pin container images by digest, inspect diffs and sensitive-path findings, and record evidence for each acceptance criterion. Keep Docker and the host OS under trusted administrative control.
