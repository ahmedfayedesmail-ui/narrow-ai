#!/usr/bin/env python3
"""Run a deterministic NARROW lifecycle demo in a disposable temporary repo.

The demo uses a local fake agent. It never contacts a model provider and never
modifies the repository containing this script. Its temporary project is kept
on disk so a person can inspect the final task workspace.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))


def run(command: list[str], *, cwd: Path, env: dict[str, str], expected: set[int] = {0}) -> subprocess.CompletedProcess[str]:
    print("\n$", " ".join(command))
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.returncode not in expected:
        raise RuntimeError(f"Command exited {result.returncode}: {' '.join(command)}")
    return result


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, stdout=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NARROW's local fake-agent end-to-end demo.")
    parser.add_argument("--scenario", choices=("repaired", "blocked"), default="repaired")
    args = parser.parse_args()

    demo_root = Path(tempfile.mkdtemp(prefix=f"narrow-demo-{args.scenario}-")).resolve()
    project = demo_root / "sample-project"
    project.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if os.name == "nt":
        env["LOCALAPPDATA"] = str(demo_root / "local-app-data")
    else:
        env["XDG_CACHE_HOME"] = str(demo_root / "cache")

    (project / "src").mkdir()
    (project / "checks").mkdir()
    (project / "tools").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    (project / "checks" / "verify.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "actual = Path('src/app.py').read_text(encoding='utf-8').strip()\n"
        "sys.exit(0 if actual == 'VALUE = 2' else 1)\n",
        encoding="utf-8",
    )
    (project / "tools" / "fake_agent.py").write_text(
        "from pathlib import Path\n"
        "import re, sys\n"
        "prompt = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        "attempt_match = re.search(r'^Attempt: (\\d+)$', prompt, re.MULTILINE)\n"
        "if not attempt_match: raise SystemExit('NARROW attempt number missing from prompt')\n"
        "attempt = int(attempt_match.group(1))\n"
        "scenario = sys.argv[2]\n"
        "value = 1 if scenario == 'blocked' or attempt == 1 else 2\n"
        "Path('src/app.py').write_text(f'VALUE = {value}\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    git(project, "init", "-b", "main")
    git(project, "config", "user.name", "NARROW Demo")
    git(project, "config", "user.email", "narrow-demo@example.invalid")
    git(project, "add", ".")
    git(project, "commit", "-m", "Create disposable demo project")

    run([sys.executable, "-m", "narrow", "init"], cwd=project, env=env)
    config_path = project / ".narrow" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agents"] = {
        "demo": {
            "command": [sys.executable, "tools/fake_agent.py", "{prompt_file}", args.scenario]
        }
    }
    config["checks"] = {
        "unit": {
            "command": [sys.executable, "checks/verify.py"],
            "timeout_seconds": 30,
        }
    }
    config["max_repair_attempts"] = 2
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    run(
        [
            sys.executable, "-m", "narrow", "start",
            "--title", "Repair the sample value",
            "--goal", "Change src/app.py so VALUE equals 2.",
            "--allow", "src/**",
            "--accept", "src/app.py defines VALUE = 2.",
        ],
        cwd=project,
        env=env,
    )

    from narrow.core import Store, task_workspace

    store = Store(project)
    task = store.get_task()
    task_id = task["id"]
    print(f"\nDemo scenario: {args.scenario}")
    print(f"Task ID: {task_id}")
    drive = run(
        [
            sys.executable, "-m", "narrow", "drive",
            "--task", task_id,
            "--agent", "demo",
            "--max-attempts", "2",
            "--yes-checks",
        ],
        cwd=project,
        env=env,
        expected={0, 2},
    )
    final_task = Store(project).get_task(task_id)
    workspace = task_workspace(final_task)
    expected_status = "READY_FOR_REVIEW" if args.scenario == "repaired" else "NEEDS_REVIEW"
    if final_task["status"] != expected_status:
        raise RuntimeError(f"Expected {expected_status}, got {final_task['status']}.")
    if args.scenario == "repaired" and drive.returncode != 0:
        raise RuntimeError("The repaired scenario did not reach the review gate.")
    if args.scenario == "blocked" and drive.returncode != 2:
        raise RuntimeError("The blocked scenario did not stop at the bounded failure gate.")

    print("\n--- Final task status ---")
    print(f"{final_task['status']}  ({task_id})")
    print(f"Temporary project: {project}")
    print(f"Isolated workspace: {workspace}")
    print("\n--- Changed paths ---")
    run(["git", "-C", str(workspace), "status", "--short", "--untracked-files=all"], cwd=project, env=env)
    print("\n--- Actual source diff ---")
    run(["git", "-C", str(workspace), "diff", "--no-ext-diff", "--", "src/app.py"], cwd=project, env=env)

    if args.scenario == "repaired":
        print("\nThe checks passed, but NARROW intentionally stopped for human review.")
        print("Review the isolated diff and acceptance criterion before running these commands:")
        print(f'  cd "{project}"')
        print(f"  {sys.executable} -m narrow inspect --task {task_id}")
        print(f'  {sys.executable} -m narrow accept --task {task_id} --criterion 1 --evidence "I reviewed the current diff and confirmed the criterion."')
        print(f"  {sys.executable} -m narrow finish --task {task_id}")
    else:
        print("\nNARROW stopped at NEEDS_REVIEW after the bounded attempts. It did not claim completion.")
        print(f'Inspect the preserved project at: {project}')
    print("\nThis is a fake-agent workflow demo—not a live Claude/Codex or sandbox security test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
