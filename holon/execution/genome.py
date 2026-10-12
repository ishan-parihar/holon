"""The department genome: six standard roles and the profiles that combine them into slots.

Every department is the same holon. A profile says which role slots it has; a slot is filled
by one or more employees, each tailored to the department through its job description.
Profiles ship in holon/genome/*.toml; $HOLON_HOME/genome/<name>.toml overrides or adds.
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

from holon.core.paths import Home

# role -> (duties it performs in today's pipeline, its skill, its function)
ROLES: dict[str, dict] = {
    "head": {"duties": ["review"], "skill": "holon-head",
             "function": "owns outcomes: shapes work, approves within authority, assigns, staffs, reviews the department"},
    "researcher": {"duties": [], "skill": "holon-researcher",
                   "function": "maps the terrain and writes it to the department knowledge base"},
    "planner": {"duties": ["plan"], "skill": "holon-planner",
                "function": "turns shaped work and terrain into a graded plan or a decomposition"},
    "executor": {"duties": ["execute"], "skill": "holon-executor",
                 "function": "does the approved plan and documents it"},
    "verifier": {"duties": ["verify"], "skill": "holon-verifier",
                 "function": "independently checks results against acceptance criteria"},
    "steward": {"duties": [], "skill": "holon-steward",
                "function": "keeps the department healthy and learning: retros, memory, playbooks, genome"},
}
# for employees hired before roles existed
ROLE_OF_DUTY = {"review": "head", "plan": "planner", "execute": "executor", "verify": "verifier"}


class GenomeError(ValueError):
    pass


def duties_for(roles: list[str]) -> list[str]:
    bad = set(roles) - set(ROLES)
    if bad:
        raise GenomeError(f"unknown role(s) {sorted(bad)}; roles are {list(ROLES)}")
    return sorted({d for r in roles for d in ROLES[r]["duties"]})


def infer_roles(duties: list[str], is_head: bool) -> list[str]:
    roles = {ROLE_OF_DUTY[d] for d in duties if d in ROLE_OF_DUTY}
    if not is_head:
        roles.discard("head")
    else:
        roles.add("head")
    return sorted(roles)


def profile_paths(home: Home | None) -> dict[str, Path | object]:
    found: dict = {p.name[:-5]: p for p in resources.files("holon.genome").iterdir() if p.name.endswith(".toml")}
    if home and (home.root / "genome").is_dir():
        found.update({p.stem: p for p in (home.root / "genome").glob("*.toml")})
    return found


def load_profile(ref: str, home: Home | None = None) -> dict:
    """A profile name (stdlib or instance) or a path to a TOML file."""
    path = Path(ref).expanduser()
    if ref.endswith(".toml") and path.exists():
        text = path.read_text()
    else:
        paths = profile_paths(home)
        if ref not in paths:
            raise GenomeError(f"no genome profile {ref!r}; available: {sorted(paths)}")
        text = paths[ref].read_text()
    return validate(tomllib.loads(text))


def validate(g: dict) -> dict:
    slots = g.get("slots") or []
    if not g.get("name") or not slots:
        raise GenomeError("a profile needs a name and at least one [[slots]] entry")
    names, covered = set(), set()
    for s in slots:
        if not s.get("name") or not s.get("roles"):
            raise GenomeError("every slot needs a name and roles")
        if s["name"] in names:
            raise GenomeError(f"duplicate slot {s['name']}")
        names.add(s["name"])
        duties_for(s["roles"])
        covered |= set(s["roles"])
        if {"executor", "verifier"} <= set(s["roles"]):
            raise GenomeError(f"slot {s['name']}: one slot may not both execute and verify")
        s.setdefault("min", 1)
    missing = set(ROLES) - covered - ({"executor"} if g.get("delegates_execution") else set())
    if missing:
        raise GenomeError(f"profile {g['name']} leaves roles unfilled: {sorted(missing)}")
    return {"name": g["name"], "description": g.get("description", ""), "slots": slots,
            "delegates_execution": bool(g.get("delegates_execution"))}


def staffing(genome: dict, employees: list[dict]) -> dict:
    """Which slots are filled, by whom, and which separation rules the current staff breaks."""
    out = []
    for s in genome["slots"]:
        holders = [e for e in employees if set(s["roles"]) <= set(e["roles"])]
        out.append({"slot": s["name"], "roles": s["roles"], "min": s["min"],
                    "filled_by": [f"{e['name']} ({e['id']})" for e in holders], "ok": len(holders) >= s["min"]})
    warnings = []
    executors = {e["id"] for e in employees if "executor" in e["roles"]}
    verifiers = {e["id"] for e in employees if "verifier" in e["roles"]}
    if executors and (not verifiers or (len(executors) == 1 and verifiers <= executors)):
        warnings.append("no verifier independent of the executor: results wait for a verifier from another department")
    return {"profile": genome["name"], "slots": out, "complete": all(s["ok"] for s in out), "warnings": warnings}
