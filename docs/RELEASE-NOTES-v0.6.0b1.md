# NARROW 0.6.0b1 — Beta preview

Date: 2026-11-03  
Status: Public Beta preview on GitHub `main`; source-only and not yet published to PyPI.

## Included

- Bounded project discovery and human-reviewed task proposals.
- Optional Docker/Podman verification with disabled networking and resource limits.
- Opt-in POSIX/local-Docker Codex agent container with an execution-scoped internal network, separate egress proxy uplink, digest-pinned images, credential broker, and ordered lifecycle cleanup.
- Third-party provider adapter extension point and expanded safety documentation.

## Validation status at this snapshot

- PR #16 is merged. Its Ubuntu/Python 3.11–3.14, Windows repaired/blocked, and package build/install jobs passed on run [#41716857214](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41716857214). The macOS job was still queued at the recorded check.
- PR #17 is merged. Main-push workflow run [#41717982650](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41717982650) was queued at the recorded check and includes the real Docker proxy-only egress acceptance job. No pass is claimed yet.
- No authenticated live Codex account session was performed. The project owner has explicitly decided that such a session is not required for the Beta label. This is not a claim of live Codex execution success.

## Boundaries

The Codex container runtime is opt-in, experimental, and supported only on POSIX hosts using the local Docker default context. The Docker daemon and host kernel remain trusted. The mounted provider API key is readable by code inside the agent container. This is not a microVM, a universal sandbox, or an independent security review.

Windows container supervision and credential DACL support, cross-platform fault injection, adversarial boundary testing, and independent security review remain open. The already-published v0.5.1 package remains Alpha and unchanged.
