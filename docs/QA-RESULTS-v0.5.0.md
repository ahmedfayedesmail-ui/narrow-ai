# NARROW v0.5.0 Alpha — QA Status

Date: 2026-11-03

## New regression coverage

- Same-task lock contention across separate processes.
- Independent locks for different task IDs.
- POSIX inherited descriptor lifetime after the NARROW-side context exits.
- Rejection of invalid task IDs, symlinked lock directories, and hard-linked lock files.

## Validation status

The updated GitHub Actions workflow checks the gzip archive CRC, extracts the release archive, compiles the source, and runs the suite on Python 3.11–3.14.

The Windows inherited-handle path, authenticated Claude/Codex end-to-end execution, detached-grandchild behavior, and independent OS-sandbox escape testing remain unvalidated. No claim of machine-level isolation is made.
