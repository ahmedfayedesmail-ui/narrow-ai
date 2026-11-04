# NARROW v0.4.0 legacy pre-Beta — QA Results

Date: 2026-11-03
Environment: Linux, Python 3.13, Git 2.47

## Local results

Command:

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -q
```

Result: **79 tests passed**.

Coverage includes:

- Git-copy task isolation and preservation of the original checkout.
- Path scope classification, reserved-file integrity, unsafe symlink/hard-link detection, and contract tampering detection.
- Snapshot-bound verification, stale-evidence invalidation, human acceptance, and bounded repair attempts.
- Provider command construction, structured event parsing, session continuity, prompt privacy, and credential/command-output minimization.
- Claude direct-file hook policy, task/subagent lifecycle event redaction, and generated sandbox settings.
- Codex `workspace-write` and `--ask-for-approval never` command construction.
- Claude and Codex reasoning-effort argument construction and enum validation.
- Dashboard loopback binding, Host validation, read-only methods, sanitized task payloads, and local-only assets.
- CLI parsing, task listing, report export, and doctor/provider diagnostics.

## GitHub Actions

The repository workflow runs on Ubuntu with Python 3.11, 3.12, 3.13, and 3.14. This is CI configuration, not evidence that maintainers have manually validated every version/platform combination. The v0.4.0 branch CI must pass before the update is merged.

## Explicitly not covered by local unit tests

- Authenticated Claude Code or Codex CLI end-to-end sessions.
- Model-specific availability of effort levels.
- Real provider subagent fan-out, cancellation, restart recovery, or child-process failure cases.
- Independent escape tests for Claude's shell sandbox or Codex's OS sandbox.
- OS-level isolation of arbitrary agent/check processes.
- Windows, macOS, Android/Termux, containers, or remote execution platforms.
- Independent security review.

A passing unit suite does not make NARROW a stable or security-certified product. See `docs/THREAT_MODEL.md`, `docs/SAFETY_MODEL.md`, and `SECURITY.md`.
