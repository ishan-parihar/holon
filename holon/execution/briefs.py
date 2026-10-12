"""Briefs: the harness-neutral handoff between the kernel and an agent.

The brief for a duty is rendered from its role skill (BRIEF_OF_KIND), resolved through the skill
layers, so a department's tailored playbook is what its employees receive.

    runs/<run_id>/BRIEF.md     role instructions (any harness can read markdown)
    runs/<run_id>/brief.json   the same, machine-readable
    runs/<run_id>/context.md    compiled context bundle
    runs/<run_id>/result.json   WRITTEN BY THE AGENT: the result envelope
    runs/<run_id>/driver.log    driver stdout/stderr (push mode only)
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from holon.capabilities.skills import Library
from holon.core.db import now
from holon.core.paths import Home

ROLE_OF_KIND = {"plan": "planner", "execute": "executor", "verify": "verifier", "review": "reviewer"}
BRIEF_OF_KIND = {"plan": ("holon-planner", "briefs/plan.md"), "execute": ("holon-executor", "briefs/execute.md"),
                 "verify": ("holon-verifier", "briefs/verify.md"), "review": ("holon-head", "briefs/review.md")}
KIND_OF_ROLE = {v: k for k, v in ROLE_OF_KIND.items()}


def holon_command(home: Home) -> str:
    """How an agent invokes the kernel from any shell. Prefers an installed `holon` command so a
    harness can allowlist `holon` without allowlisting the Python interpreter."""
    exe = "holon" if shutil.which("holon") else f"{shlex.quote(sys.executable)} -m holon"
    return f"{exe} --home {shlex.quote(str(home.root))}"


def kernel_env(home: Home) -> dict[str, str]:
    pkg_parent = str(Path(__file__).resolve().parents[2])
    pythonpath = os.pathsep.join(p for p in (pkg_parent, os.environ.get("PYTHONPATH", "")) if p)
    return {"HOLON_HOME": str(home.root), "PYTHONPATH": pythonpath, "HOLON": holon_command(home)}


def build(home: Home, run: dict, task: dict, context_md: str, library: Library | None = None,
          dept: str | None = None) -> Path:
    kind = run["kind"]
    d = home.run_dir(run["id"])
    d.mkdir(parents=True, exist_ok=True)
    result_path = d / "result.json"
    if result_path.exists():
        result_path.unlink()
    workdir = task.get("workdir") or str(d)
    (d / "context.md").write_text(context_md)
    skill, file = BRIEF_OF_KIND[kind]
    lib = library or Library(home)
    md = lib.render(lib.read(skill, file, dept), dict(
        run_id=run["id"], task_id=task["id"], context_md=str(d / "context.md"), result_path=str(result_path),
        workdir=workdir, holon=holon_command(home)))
    (d / "BRIEF.md").write_text(md)
    (d / "brief.json").write_text(json.dumps({
        "run_id": run["id"], "task_id": task["id"], "kind": kind, "role": ROLE_OF_KIND[kind],
        "brief_md": str(d / "BRIEF.md"), "context_md": str(d / "context.md"), "result_path": str(result_path),
        "workdir": workdir, "holon": holon_command(home), "lease_until": run.get("lease_until"),
        "envelope": f"schemas/{kind}-result.schema.json", "skill": skill, "created_at": now()}, indent=2))
    return d


def load(home: Home, run_id: str) -> dict:
    return json.loads((home.run_dir(run_id) / "brief.json").read_text())
