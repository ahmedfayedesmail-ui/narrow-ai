# NARROW v0.5.0 Alpha — QA Status

Date: 2026-11-03

## New regression coverage

- Same-task lock contention across separate processes.
- Independent locks for different task IDs.
- POSIX inherited descriptor lifetime after the NARROW-side context closes.
- Rejection of invalid task IDs, symlinked lock directories, and hard-linked lock files.

## CI evidence

- Ubuntu / Python 3.11, 3.12, 3.13, and 3.14: checked-in source and extracted release-archive test suites passed.
- Windows Server 2025 / Python 3.13: task-lock tests and CLI smoke test passed. Five lock tests passed; the POSIX inherited-descriptor test was intentionally skipped on Windows.
- Archive gzip integrity, extraction, source compilation, and CLI smoke checks passed in the completed Linux matrix run.

## Still unvalidated

- Windows inherited-handle crash lifetime (the Windows run validates lock contention, not lease survival after the parent process exits).
- Authenticated Claude Code and Codex CLI end-to-end sessions.
- macOS runtime behavior.
- Detached-grandchild behavior and independent OS-sandbox escape testing.

NARROW's execution lease is process coordination, not a machine-level sandbox or a correctness guarantee.
