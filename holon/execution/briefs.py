"""Briefs: the harness-neutral handoff between the kernel and an agent.

The brief for a stage is rendered from its role skill (`BRIEF_OF_STAGE`), resolved through the skill
layers, so a department's tailored playbook is what its employees receive.

    runs/<run_id>/BRIEF.md      stage instructions (any harness can read markdown)
    runs/<run_id>/brief.json    the same, machine-readable (includes the run token and the department's
                               resolved secrets: mode 0600)
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
from holon.execution import policy as run_policy

from holon.core import vocab

BRIEF_OF_STAGE = {s: (f"holon-{vocab.STAGE_ROLE[s]}", f"briefs/{s}.md") for s in vocab.STAGES}

# The exploration budget, rendered into every stage's brief (R2, the 2026-10-20 bridge plan).
# One place, one number, every stage — so a body can follow it and a reader sees one rule.
#
# The measurement that made it a rule: one body spent 66 tool calls and hit its
# 40-iteration cap hunting `schemas/plan-result.schema.json` — a file the brief never asks
# for — while the brief had already named the exact path to write and the exact JSON shape.
# A budget a body can follow carries a number; a budget without one is a hope.
BUDGET = ("Read the context and your skill, and consult **at most three more sources**. The result "
          "shape is in this brief — do not search for a schema, a template or a reference file, and do "
          "not read Holon's own source to plan. When the budget is spent, write the result with what "
          "you have: a thin result is rework, an unfinished run is nothing.")


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
          token: str | None = None, scope: dict | None = None, policy: dict | None = None,
          secrets: dict[str, str] | None = None) -> Path:
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
    variables = dict(
        run_id=run["id"], task_id=node["id"], work_id=node["parent_id"] or node["id"], stage=stage,
        context_md=str(d / "context.md"), result_path=str(result_path), workdir=workdir, holon=holon,
        envelope_path=str(d / "envelope.schema.json"))
    md = lib.render(lib.read(skill, file, dept), variables)
    # R2 (the 2026-10-20 bridge plan): the exploration budget is rendered here, once, for
    # every stage — not copied into each brief. Seven copies of one paragraph is exactly
    # the redundancy the bridge plan set out to remove, and the kernel's own lint says so
    # ("dry.paragraph"). The budget is the kernel's rule, so the kernel states it.
    md = md.rstrip() + "\n\n## Your budget\n\n" + BUDGET
    if scope and (scope["roots"] or scope.get("read_roots") or scope["commands"] or scope["act_commands"]):
        cmds = scope["commands"] + (scope["act_commands"] if vocab.POSTURE[stage] == "act" else [])
        md = md.rstrip() + "\n\n## Your reach\n\n" + (
            f"Beyond `{workdir}` and this run's directory, you may work in: "
            + ", ".join(f"`{r}`" for r in scope["roots"]) + ".\n" if scope["roots"] else "") + (
            "You may read (never write): " + ", ".join(f"`{r}`" for r in scope.get("read_roots") or [])
            + " — pooled units are their owners' work; ask through `msg request`.\n" if scope.get("read_roots") else "") + (
            "Commands granted to your department: " + ", ".join(f"`{c}`" for c in cmds) + ".\n" if cmds else "") + \
            "Anything else is outside your department's scope: say so in your result instead of working around it.\n"
    # G-d (measured 2026-10-21): the observe posture's own commands were compiled into policy.json
    # and rendered nowhere, so a body that could read policy.json saw a list it had been told
    # nothing about — and a body that could not had no shell at all. The reach section is where a
    # body learns what it may run; state the posture's own list here, once, for every stage, so the
    # machine-readable policy and the human-readable brief cannot disagree.
    if vocab.POSTURE[stage] == "observe":
        md = md.rstrip() + "\n\n## Reading what you plan\n\n" + (
            "You may run these read-only commands (they are in this run's `policy.json`): "
            + ", ".join(f"`{c}`" for c in run_policy.observe_commands()) + ".\n\n"
            "Use them to see the current state before you plan it. Anything that would change state "
            "is not yours at this stage: the executor runs with a different reach, so plan the "
            "change and let it make it.\n")
    (d / "BRIEF.md").write_text(md)
    # R-09: the run carries the department-resolved library, so a body that loads skills from
    # disk sees one copy of every skill — a department fork is the only version it is offered.
    # Deliberately not registered with the harness exports: a run's snapshot is written once,
    # before the body runs, and is never refreshed underneath it.
    # M2 (2026-10-24): "one copy of every skill" means one copy of every skill *this employee
    # may hold*. The whole-library export measured 50,325 chars where an executor's own
    # membership rule names 13,303 — every role the employee does not hold is a token cost the
    # brief never asked for. The rule is the same one `skill list --as` ships and tests
    # (protocol skills + one holon-<role> per role held + the employee's extra skills), so this
    # applies an existing policy at one more site; the stage's own role skill is added whether
    # or not the record names it, and `skill show` still resolves any skill by name from the
    # library — nothing is lost, only un-auto-loaded.
    names: list[str] | None = None
    if employee:
        names = sorted({"holon-employee", "holon-collaboration", "holon-memory"}
                      | {f"holon-{r}" for r in employee["roles"]}
                      | set(employee.get("skills") or []) | {skill})
    lib.export(d / "skills", variables, names=names, dept=dept)
    # The envelope's schema, written beside the brief rather than pointed at in the distribution.
    # A body that has to know the exact shape it must write was previously told "do not read
    # Holon's source" while the distribution — which holds schemas/ — was in its read reach, so the
    # one artifact it needed was both visible and forbidden and it spent its budget hunting for it.
    # Rendered from the contract (the one writer of the rules, law 5), so it can never drift.
    from holon.core import contract as _contract
    (d / "envelope.schema.json").write_text(_contract.schema_text(stage))
    if policy:
        (d / "policy.json").write_text(json.dumps(policy, indent=2))
        try:
            (d / "policy.json").chmod(0o640)   # the enforcing harness must read it (CONTRACT §10, H-07)
        except OSError:   # pragma: no cover - the mode is advisory outside isolation
            pass
    bj = d / "brief.json"
    bj.write_text(json.dumps({
        "run_id": run["id"], "task_id": node["id"], "work_id": node["parent_id"], "stage": stage,
        "role": vocab.STAGE_ROLE[stage], "posture": vocab.POSTURE[stage],
        "employee_id": employee["id"] if employee else None, "employee_name": employee["name"] if employee else None,
        "employee_slug": slug(employee["name"]) if employee else None,
        "brief_md": str(d / "BRIEF.md"), "context_md": str(d / "context.md"),
        "skills": str(d / "skills"),
        "identity_md": str(d / "identity.md") if identity_md else None, "result_path": str(result_path),
        "workdir": workdir, "holon": holon, "lease_until": run.get("lease_until"), "token": token,
        "envelope": str(d / "envelope.schema.json"), "skill": skill, "scope": scope or {"name": "org"},
        "policy": str(d / "policy.json") if policy else None,
        "secrets": secrets or {},   # G8: dept key files, resolved at claim; 0600 like the token above
        "created_at": now()}, indent=2))
    bj.chmod(0o600)
    return d


def slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "employee"


def load(home: Home, run_id: str) -> dict:
    return json.loads((home.run_dir(run_id) / "brief.json").read_text())
