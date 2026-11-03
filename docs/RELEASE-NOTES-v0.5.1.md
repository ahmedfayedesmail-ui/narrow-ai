# NARROW v0.5.1 Alpha — Release Notes

Release date: 2026-11-03

## Packaging and documentation refresh

This is a patch release for the Python distribution and its public package-index presentation. There are no application runtime behavior changes from v0.5.0.

- Refresh the README content embedded in wheel and source distribution metadata.
- Add project, documentation, issue tracker, and changelog URLs to package metadata.
- Validate the source archive and wheel with strict Twine metadata checks.
- Publish through GitHub OIDC Trusted Publishing, with TestPyPI upload and clean-index installation verification before production PyPI.

## Runtime behavior

The task-scoped execution lease, provider adapters, bounded repair/handoff, local Control Center, scope monitoring, and human-acceptance behavior are unchanged from v0.5.0.

## Safety and validation limitations

NARROW remains Alpha software. CI and package-install tests do not replace authenticated Claude Code/Codex CLI end-to-end testing, Windows crash-lifetime and detached-process adversarial testing, full OS sandbox escape assessment, or independent security review. A Git worktree is not an OS sandbox. Review verification commands and use a separately restricted VM/container for untrusted code.
