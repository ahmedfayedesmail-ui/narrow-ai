# Security Policy

Please do not report exploitable vulnerabilities in public issues.

Until a dedicated security contact is configured, report concerns privately to the repository owner through GitHub's private vulnerability reporting feature when available. Include the affected version, impact, reproduction steps, and any suggested mitigation. Do not include secrets or personal data in the report.

The current source on `main` is the NARROW v0.6.0b1 Beta preview. The older v0.5.0 and v0.5.1 distributions are legacy pre-Beta packages. A task's isolated Git copy is not an operating-system sandbox. Native Claude and Codex profiles request provider-managed boundaries (Claude's fail-closed shell sandbox plus selected direct-tool hooks; Codex's workspace-write sandbox), but neither is claimed to be a complete disposable VM or a universal containment layer. The generic `--agent` adapter and direct `run`/`resume` commands do not receive an OS sandbox from NARROW. Verification commands retain the invoking user's permissions. Do not use NARROW for unattended execution of untrusted agents, repositories, or checks without an independently configured disposable OS sandbox.


## Execution lease limitation

NARROW v0.5.0 serializes operations per task and passes its lock handle to the first-level agent/check process. This is not an operating-system sandbox. Detached descendants may outlive the lease, and Windows crash-lifetime behavior remains under validation. After an unexpected supervisor exit, inspect active processes and the isolated workspace before restarting.


## Security boundary summary

The current guarantees and residual risks are documented in [the v0.5.0 threat model](docs/THREAT_MODEL.md) and [safety model](docs/SAFETY_MODEL.md). In particular, repository monitoring is not an OS security boundary; direct provider hooks cover only selected tools; arbitrary verification programs execute with user permissions; same-user local processes and detached descendants remain outside NARROW's complete control. Please include the exact entry point and provider CLI version in vulnerability reports, and never attach credentials or private project content.
