# NARROW v0.5.0 Alpha — Release Notes

Release date: 2026-11-03

## Task-scoped execution lease

NARROW now serializes operations that can execute or judge work in one task workspace:

- `narrow drive` holds one lease for the complete bounded agent → verification → repair loop.
- `narrow run` and `narrow resume` acquire the same lease before starting an agent process.
- `narrow verify` and `narrow finish` refuse to race an active task execution.

A second NARROW process targeting the same task receives a clear "already active" error. Separate tasks can proceed independently.

### Supervisor interruption

The first-level agent/check process inherits the lease handle. On POSIX, the inherited descriptor keeps the OS lock held if the NARROW supervisor exits while the child is still running. This prevents an immediate second NARROW process from starting another writer against the same workspace.

### Safety boundary

This is execution coordination, not a sandbox. It does not reduce child OS permissions, prevent malicious code from deliberately closing inherited descriptors, or guarantee detached grandchildren have ended. The Windows inheritable-handle implementation requires dedicated crash-lifetime validation with CI and real providers.

After an unexpected supervisor exit, inspect active processes and the isolated workspace before restarting.

## Repository layout

The full source tree and test suite are now visible at repository root. The versioned archive remains available for convenient installation and reproducible release checks.
