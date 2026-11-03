# NARROW end-to-end demo

The demo runs NARROW's real CLI and task lifecycle against a tiny temporary Git repository. It uses a deterministic local fake agent so it needs no Claude/Codex account, API key, network access, or external repository.

It demonstrates two outcomes:

- `repaired`: attempt 1 fails the configured check; NARROW records the failure, creates a handoff, starts attempt 2, and reaches `READY_FOR_HUMAN_REVIEW`.
- `blocked`: both bounded attempts fail; NARROW stops at `NEEDS_REVIEW` instead of claiming success.

## Requirements

- Python 3.11 or newer
- Git installed and available on PATH
- The NARROW source checkout

## Run

From the NARROW repository root:

```bash
PYTHONPATH=src python examples/demo.py --scenario repaired
PYTHONPATH=src python examples/demo.py --scenario blocked
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python examples/demo.py --scenario repaired
python examples/demo.py --scenario blocked
```

The script creates a uniquely named temporary project and cache, prints the task status and actual task-workspace diff, and leaves the demo project on disk so it can be inspected. It never edits the repository from which the demo is launched. The printed path identifies the temporary project.

For the repaired scenario, the script prints the commands for a person to review and accept the result. It deliberately does **not** run `narrow accept` or `narrow finish` automatically: human acceptance must remain a real human decision.

## What this demo proves—and does not prove

It exercises the CLI, isolated Git copy, task contract, generic command adapter, bounded retry, failure evidence, handoff/resume context, check execution, and review gate with a deterministic fake agent.

It is not an authenticated Claude Code or Codex integration test, a sandbox escape test, an independent security review, or proof of correctness. See [the provider validation status](PROVIDERS.md), [the threat model](THREAT_MODEL.md), and [the QA record](QA-RESULTS-v0.5.0.md).
