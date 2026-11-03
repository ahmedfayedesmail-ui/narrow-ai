# NARROW v0.5.0 Alpha — QA Status

Date: 2026-11-03
Validated main snapshot: `7f52ee9387fe1c254d8e7695e916cfece2effeb3`

## Regression coverage

- Same-task lock contention across separate processes.
- Independent locks for different task IDs.
- POSIX inherited descriptor lifetime after the NARROW-side context closes.
- Rejection of invalid task IDs, symlinked lock directories, and hard-linked lock files.
- Local NARROW metadata stays out of Git status through the repository-local exclude mechanism.
- Canonical repository-root comparison is portable across macOS temporary-directory symlinks.
- Control Center counts the persisted `READY_FOR_REVIEW` state correctly.
- Human review and completion remain separate; a green check does not auto-accept a task.

## CI evidence

The validated snapshot passed all 7 GitHub Actions jobs in run [#41644644961](https://github.com/ahmedfayedesmail-ui/narrow-ai/actions/runs/41644644961):

- **Ubuntu / Python 3.11, 3.12, 3.13, and 3.14:** checked-in unit suites, repaired and blocked demos, fresh source-archive creation, extracted archive test suites, and CLI smoke tests passed.
- **macOS / Python 3.13:** full unit suite (87 tests) and repaired/blocked demos passed on the GitHub-hosted runner.
- **Windows Server 2025 / Python 3.13:** task-lock tests and CLI smoke passed; the POSIX inherited-descriptor test is intentionally skipped on Windows. Repaired and blocked demos passed.
- **Packaging / Ubuntu / Python 3.13:** wheel and source distribution built successfully; the wheel installed in a clean virtual environment and its CLI version command passed.
- The tested source archive was generated from the exact merged main commit and uploaded as a workflow artifact. The old hand-maintained `narrow-v0.5.0.tar.gz` snapshot was removed to avoid presenting a stale archive as the current source.

## Still unvalidated

- Authenticated live Claude Code and Codex CLI end-to-end sessions, including real session resume and supported model/CLI compatibility.
- Windows inherited-handle crash lifetime; the Windows CI run validates contention, not lease survival after the parent process exits.
- Detached-grandchild and adversarial process-tree behavior.
- Complete OS sandbox escape resistance for every provider tool/helper, generic adapter, and verification command.
- Independent security review.
- Android/Termux support.

NARROW's execution lease is process coordination, not a machine-level sandbox. Passing CI does not prove semantic correctness or establish production security. Until the remaining gates are completed, use this Alpha on low-risk or disposable repositories with reviewed commands and manual diff acceptance.
