# NARROW — Product Architecture and Capabilities

NARROW separates task orchestration, agent execution, verification, evidence review, and human completion into explicit layers. The design keeps the developer in control while making AI-assisted work repeatable and observable.

## 1. Task contract and workspace

Each task defines its goal, allowed paths, denied paths, acceptance criteria, and execution budgets. NARROW creates a dedicated Git workspace and maintains a local event journal. Task-level execution locks prevent overlapping agent, verification, and finish operations on the same task.

## 2. Provider orchestration

NARROW supports native Claude Code and Codex CLI adapters, plus configured generic command adapters. Structured provider events, task handoffs, bounded prompts, and compact local continuity records keep the work understandable across sessions.

## 3. Change monitoring

The task workspace is monitored against the approved path contract. Provider hooks apply additional direct-file checks where the provider exposes the required hook surface. Change summaries and unified diffs are available in the local Control Center.

## 4. Verification and repair

Verification commands are explicitly configured as argv arrays. Results are signed and tied to the workspace snapshot and configured command. A later workspace change makes the previous result stale.

Repair attempts are bounded. Before an automatic repair continuation, NARROW runs its deterministic review policy. Plain verification failures may continue within the configured budget; scope, integrity, credential-path, or security-sensitive findings are preserved for human review.

## 5. Deterministic evidence review

The review gate checks task-contract integrity, workspace symlinks and hard links, allowed/denied path scope, change-count budget, diff hygiene, original-checkout and task-branch invariants, signed verification evidence and snapshot freshness, human acceptance evidence freshness, and credential-like or security-sensitive file paths.

The review report is evidence triage. It does not determine semantic correctness by itself.

## 6. Human decision and completion

The local Control Center and `narrow review-action` expose criterion confirmation, request-changes, reject, and approve actions. Each acceptance is bound to the current workspace snapshot. Approval calls the independent finish gate again; a task cannot be marked verified complete solely because an agent reports success or checks pass.

## 7. Execution and trust boundaries

Native providers use their own execution enforcement. The Docker agent profile is available on POSIX hosts using the local Docker context and includes a digest-pinned agent image, an execution-scoped internal network, an egress proxy sidecar, task-scoped credentials, and supervised cleanup. Verification-container configuration is separate from agent execution.

The Docker daemon and host kernel remain trusted infrastructure. A provider key mounted for a container is readable by the provider process and tools running in that container. Generic command adapters and direct low-level commands use the invoking user's permissions unless independently constrained. See [SECURITY.md](../SECURITY.md) and [the threat model](THREAT_MODEL.md) for exact guarantees and assumptions.

## 8. Local-first data flow

Task contracts, event history, review records, and check evidence remain in local project state. NARROW does not require a hosted NARROW account. Provider requests are made by the configured provider CLI; optional proposal generation can use an already-running local Ollama service.
