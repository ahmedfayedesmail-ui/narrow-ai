# NARROW 0.6.0b1 — Historical Source Snapshot

Original snapshot date: 2026-11-03  
Superseded by: NARROW 1.0.0

## Included in this source generation

- Bounded project discovery and human-reviewed task proposals.
- Optional Docker/Podman verification with disabled networking and resource limits.
- POSIX/local-Docker Claude/Codex agent execution with an execution-scoped internal network, egress proxy uplink, digest-pinned images, credential broker, and lifecycle cleanup.
- Third-party provider adapter extension point and expanded security documentation.

## Validation record

- The merged-main product-check workflow passed its full matrix and container acceptance job.
- CodeQL passed on the same source generation.

This file is retained as historical release documentation. Current installation, usage, product capabilities, and operating boundaries are documented in the 1.0.0 README, [SECURITY.md](../SECURITY.md), and [the agent runtime architecture](AGENT_SANDBOX_DESIGN.md).
