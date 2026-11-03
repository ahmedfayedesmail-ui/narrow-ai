# NARROW

**AI can start the work. NARROW helps make sure the work is finished safely.**

NARROW is a local-first task continuity and safety supervisor for AI coding agents. It brings together:

- **Scope protection** — define allowed and restricted paths and monitor changes while an agent works.
- **Secondhand memory** — keep a compact task journal and generate handoffs so work can resume in a fresh session or with another agent.
- **Completion gates** — run explicitly configured checks, preserve failures, and distinguish an agent stopping from work being verified.
- **Bounded repair** — optionally run a limited check-and-repair cycle, with human review required before verified completion.

NARROW does not require a NARROW API key and does not send source code to a NARROW server.

## Safety first

The v0.1 workspace is a separate Git copy, **not an operating-system sandbox**. Agent processes still run with the permissions of the user who starts them and may access other files, services, or network resources. Keep the agent's own approval controls enabled. Use a container or VM for untrusted tasks.

NARROW does not commit, merge, push, publish, deploy, or modify the original checkout. Green tests alone are not proof of semantic correctness. A human must review the result and acceptance criteria.

## v0.1.0 source package

The complete source tree, tests, documentation, and GitHub templates are included in:

- [Download NARROW v0.1.0 source archive](./narrow-v0.1.0.tar.gz)

Extract the archive, then follow the full README inside the extracted `NARROW/` directory.

## Current status

**Alpha / experimental.** The initial test suite passes locally on Linux with Python 3.13 and Git 2.47. Windows, macOS, and Android are not yet validated. Provider-native Claude Code and Codex integrations and OS-level sandboxing are not included in v0.1.0.

Please read [SECURITY.md](./SECURITY.md) and the threat model in the source archive before using NARROW on valuable work.

## License

MIT. See [LICENSE](./LICENSE) in the source archive.
