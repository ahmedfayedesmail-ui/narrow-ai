# Security Policy

Please do not report exploitable vulnerabilities in public issues.

Until a dedicated security contact is configured, report concerns privately to the repository owner through GitHub's private vulnerability reporting feature when available. Include the affected version, impact, reproduction steps, and any suggested mitigation. Do not include secrets or personal data in the report.

NARROW is alpha software. The v0.2 isolated Git-copy supervisor is not an operating-system sandbox and must not be used for unattended execution of untrusted agents, repositories, or verification commands. Agent and check processes retain the current user's permissions.


## Execution lease limitation

NARROW v0.5 serializes operations per task and passes its lock handle to the first-level agent/check process. This is not an operating-system sandbox. Detached descendants may outlive the lease, and Windows crash-lifetime behavior remains under validation. After an unexpected supervisor exit, inspect active processes and the isolated workspace before restarting.
