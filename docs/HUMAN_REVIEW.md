# Human review and acceptance — v0.5.0 workflow baseline (pre-Beta)

NARROW does not approve an agent's work on the user's behalf. A passing test suite is necessary evidence, not a substitute for reviewing the actual change.

## Required review sequence

Run these commands from the original repository:

```bash
narrow status --task <task-id>
narrow inspect --task <task-id>
```

The status output prints the isolated task workspace path. Inspect that workspace, not the original checkout.

Review the complete change set:

```bash
git -C "<isolated-workspace>" status --short --untracked-files=all
git -C "<isolated-workspace>" diff --check
git -C "<isolated-workspace>" diff --no-ext-diff --no-textconv
```

The normal diff shows tracked-file changes. Also open and inspect every untracked file shown by `git status`; untracked files do not appear in `git diff` by default. Review generated files, configuration, scripts, dependency changes, and tests—not just the headline source change. Do not run unfamiliar files or scripts merely to inspect them.

Run the required verification checks after reviewing the diff:

```bash
narrow verify --task <task-id> --check <check-name>
```

NARROW records each result against the exact workspace snapshot and invalidates it after later changes. For an approved, trusted check, `--yes` skips the interactive confirmation; it does not sandbox the command.

For every acceptance criterion, explicitly record your own review and evidence:

```bash
narrow accept --task <task-id> --criterion 1 --evidence "Reviewed the current diff and confirmed the stated behavior."
```

Repeat for every criterion. Then run:

```bash
narrow finish --task <task-id>
```

The finish gate checks current passing configured checks, current-snapshot human acceptance evidence, allowed/denied paths, unsafe workspace links, the task branch and base commit, and whether the original checkout remains clean and unchanged. A failure leaves the task in a review-needed state.

## What the human is responsible for

- Decide whether the change actually satisfies the task and is maintainable.
- Look for hidden scope expansion, data deletion, security regressions, dependency changes, suspicious scripts, and misleading tests.
- Confirm the configured checks are relevant and trustworthy. Their commands are executable programs and retain the invoking user's permissions.
- Confirm every acceptance statement against the actual code and behavior, not just an agent's report.
- Integrate the result manually only after review. NARROW does not commit, merge, push, deploy, or publish it.

The `narrow accept` command records an explicit user assertion and evidence text. It cannot prove that a person genuinely read the diff or that the evidence is truthful. `VERIFIED_COMPLETE` means the documented gates passed; it is not a mathematical proof of semantic correctness or security.

For untrusted repositories or generated code, do this work in a disposable VM/container with restricted mounts, credentials, network, and process permissions. NARROW's task copy and review gate do not replace that boundary.
