# Security Policy

Please do not report exploitable vulnerabilities in public issues.

Report security concerns privately through GitHub's private vulnerability reporting feature when available. Include the affected version, impact, reproduction steps, and suggested mitigation. Do not include secrets or personal data in the report.

NARROW is alpha software. The isolated Git-copy supervisor is not an operating-system sandbox. Agent and check processes inherit the invoking user's permissions. Do not use unattended execution with untrusted agents, repositories, prompts, hooks, plugins, or verification commands. Use a restricted disposable container or VM when stronger isolation is required.

Security-sensitive behavior is release-blocking. Please include a focused regression test with any proposed fix when possible.
