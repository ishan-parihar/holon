"""Briefs: the harness-neutral handoff between the kernel and an agent.

The brief for a stage is rendered from its role skill (`BRIEF_OF_STAGE`), resolved through the skill
layers, so a department's tailored playbook is what its employees receive.

    runs/<run_id>/BRIEF.md      stage instructions (any harness can read markdown)
    runs/<run_id>/brief.json    the same, machine-readable (includes the run token: mode 0600)
    runs/<run_id>/identity.md   who the employee is (for harnesses that put it in the system prompt)
    runs/<run_id>/context.md    compiled context bundle
    runs/<run_id>/result.json   WRITTEN BY THE AGENT: the result envelope
    runs/<run_id>/trajectory.json  optional (H4): the run's ordered steps, kept with the output
    runs/<run_id>/policy.json   the run's reach + budget, compiled at claim (H3+H5)
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

from holon.core import vocab

BRIEF_OF_STAGE = {s: (f"holon-{vocab.STAGE_ROLE[s]}", f"briefs/{s}.md") for s in vocab.STAGES}


def holon_command(home: Home) -> str:
    """How an agent invokes the kernel from any shell. Prefers an installed `holon` command so a
    harness can allowlist `holon` without allowlisting the Python interpreter."""
    exe = "holon" if shutil.which("holon") else f"{shlex.quote(sys.executable)} -m holon"
    return f"{exe} --home {shlex.quote(str(home.root))}"


def kernel_env(home: Home) -> dict[str, str]:
    pkg_parent = str(Path(__file__).resolve().parents[2])
    pythonpath = os.pathsep.join(p for p in (pkg_parent, os.environ.get("PYTHONPATH", "")) if p)
    return {"HOLON_HOME": str(home.root), "PYTHONPATH": pythonpath, "HOLON": holon_command(home)}


def build(home: Home, run: dict, node: dict, context_md: str, library: Library | None = None,
          dept: str | None = None, employee: dict | None = None, identity_md: str | None = None,
          token: str | None = None, scope: dict | None = None, policy: dict | None = None) -> Path:
    stage = run["kind"]
    d = home.run_dir(run["id"])
    d.mkdir(parents=True, exist_ok=True)
    result_path = d / "result.json"
    if result_path.exists():
        result_path.unlink()
    workdir = node.get("workdir") or str(d)
    (d / "context.md").write_text(context_md)
    if identity_md:
        (d / "identity.md").write_text(identity_md)
    skill, file = BRIEF_OF_STAGE[stage]
    lib = library or Library(home)
    holon = holon_command(home)
    md = lib.render(lib.read(skill, file, dept), dict(
        run_id=run["id"], task_id=node["id"], work_id=node["parent_id"] or node["id"], stage=stage,
        context_md=str(d / "context.md"), result_path=str(result_path), workdir=workdir, holon=holon))
    if scope and (scope["roots"] or scope["commands"] or scope["act_commands"]):
        cmds = scope["commands"] + (scope["act_commands"] if vocab.POSTURE[stage] == "act" else [])
        md = md.rstrip() + "\n\n## Your reach\n\n" + (
            f"Beyond `{workdir}` and this run's directory, you may work in: "
            + ", ".join(f"`{r}`" for r in scope["roots"]) + ".\n" if scope["roots"] else "") + (
            "Commands granted to your department: " + ", ".join(f"`{c}`" for c in cmds) + ".\n" if cmds else "") + \
            "Anything else is outside your department's scope: say so in your result instead of working around it.\n"
    (d / "BRIEF.md").write_text(md)
    if policy:
        (d / "policy.json").write_text(json.dumps(policy, indent=2))
        try:
            (d / "policy.json").chmod(0o640)   # the enforcing harness must read it (CONTRACT §9, H-07)
        except OSError:   # pragma: no cover - the mode is advisory outside isolation
            pass
    bj = d / "brief.json"
    bj.write_text(json.dumps({
        "run_id": run["id"], "task_id": node["id"], "work_id": node["parent_id"], "stage": stage,
        "role": vocab.STAGE_ROLE[stage], "posture": vocab.POSTURE[stage],
        "employee_id": employee["id"] if employee else None, "employee_name": employee["name"] if employee else None,
        "employee_slug": slug(employee["name"]) if employee else None,
        "brief_md": str(d / "BRIEF.md"), "context_md": str(d / "context.md"),
        "identity_md": str(d / "identity.md") if identity_md else None, "result_path": str(result_path),
        "workdir": workdir, "holon": holon, "lease_until": run.get("lease_until"), "token": token,
        "envelope": f"schemas/{stage}-result.schema.json", "skill": skill, "scope": scope or {"name": "org"},
        "policy": str(d / "policy.json") if policy else None,
        "created_at": now()}, indent=2))
    bj.chmod(0o600)
    return d


def slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "employee"


def load(home: Home, run_id: str) -> dict:
    return json.loads((home.run_dir(run_id) / "brief.json").read_text())
