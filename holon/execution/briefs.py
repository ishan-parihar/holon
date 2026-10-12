"""Briefs: the harness-neutral handoff between the kernel and an agent.

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
import sys
from importlib import resources
from pathlib import Path
from string import Template

from holon.core.db import now
from holon.core.paths import Home

ROLE_OF_KIND = {"plan": "planner", "execute": "executor", "verify": "verifier"}
KIND_OF_ROLE = {v: k for k, v in ROLE_OF_KIND.items()}


def holon_command(home: Home) -> str:
    """How an agent invokes the kernel from any shell, without installation."""
    return f"{shlex.quote(sys.executable)} -m holon --home {shlex.quote(str(home.root))}"


def kernel_env(home: Home) -> dict[str, str]:
    pkg_parent = str(Path(__file__).resolve().parents[2])
    pythonpath = os.pathsep.join(p for p in (pkg_parent, os.environ.get("PYTHONPATH", "")) if p)
    return {"HOLON_HOME": str(home.root), "PYTHONPATH": pythonpath, "HOLON": holon_command(home)}


def build(home: Home, run: dict, task: dict, context_md: str) -> Path:
    kind = run["kind"]
    d = home.run_dir(run["id"])
    d.mkdir(parents=True, exist_ok=True)
    result_path = d / "result.json"
    if result_path.exists():
        result_path.unlink()
    workdir = task.get("workdir") or str(d)
    (d / "context.md").write_text(context_md)
    tmpl = resources.files("holon.execution").joinpath("templates", f"{kind}.md").read_text()
    md = Template(tmpl).safe_substitute(
        run_id=run["id"], task_id=task["id"], context_md=str(d / "context.md"), result_path=str(result_path),
        workdir=workdir, holon=holon_command(home))
    (d / "BRIEF.md").write_text(md)
    (d / "brief.json").write_text(json.dumps({
        "run_id": run["id"], "task_id": task["id"], "kind": kind, "role": ROLE_OF_KIND[kind],
        "brief_md": str(d / "BRIEF.md"), "context_md": str(d / "context.md"), "result_path": str(result_path),
        "workdir": workdir, "holon": holon_command(home), "lease_until": run.get("lease_until"),
        "envelope": f"schemas/{kind}-result.schema.json", "created_at": now()}, indent=2))
    return d


def load(home: Home, run_id: str) -> dict:
    return json.loads((home.run_dir(run_id) / "brief.json").read_text())
