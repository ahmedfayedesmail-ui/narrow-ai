# NARROW v0.1.0 — QA Results

## Result

The source package passes its current local automated test suite. This is an **alpha engineering check**, not an independent security assessment or a production-readiness claim.

## Environment tested

- OS: Linux x86_64 (container environment)
- Python: 3.13.5
- Git: 2.47.3
- Runtime Python dependencies: none

## Checks executed

```bash
python -m compileall -q src tests
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -q
python -m narrow --version
python -m narrow drive --help
```

Latest local test result: **41 tests passed**. The tests cover clean-checkout enforcement, isolated Git cloning, safe Git environment handling, allowlist/denylist classification, ignored files, symlink and hard-link rejection, metadata-path protections, task-contract integrity, process monitoring, timeouts, bounded verification output, snapshot-bound checks, human acceptance, and bounded repair behavior.

## Not yet validated

- Windows and macOS execution.
- The Python 3.11 and 3.12 CI matrix (CI is configured but has not been run by this QA pass).
- Real Claude Code, Codex, Cursor, Copilot, or other provider adapters. `drive` currently requires a user-configured generic prompt-file adapter; interactive `run`/`resume` can launch an installed CLI directly.
- Container/VM sandboxing, network isolation, or protection against absolute-path writes by a process running as the current user.
- Independent security review, adversarial fuzzing, and long-running soak tests.

## Release guidance

Keep this release marked **Alpha**. Use only with trusted repositories, trusted agent CLIs, and reviewed verification commands. Keep provider approval controls enabled. Do not run unattended or against untrusted inputs until the isolated runner is replaced or supplemented by a separately tested OS-level sandbox.
