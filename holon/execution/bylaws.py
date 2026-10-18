"""The department bylaws: six standard roles and the profiles that combine them into slots.

Every department is the same holon. A profile says which role slots it has; a slot is filled
by one or more employees, each tailored to the department through its job description.
Profiles ship in holon/bylaws/*.toml; $HOLON_HOME/bylaws/<name>.toml overrides or adds.
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

from holon.core import vocab
from holon.core.paths import Home

ROLE_FUNCTION = {
    "head": "owns outcomes: shapes work, approves within authority, assigns, staffs, reviews the department",
    "researcher": "maps the terrain and writes it to the department knowledge base",
    "planner": "turns shaped work and terrain into a graded plan or a decomposition",
    "executor": "does the approved plan and documents it",
    "verifier": "independently checks results against acceptance criteria",
    "steward": "keeps the department healthy and learning: retros, memory, playbooks, bylaws",
}
ROLES = {r: {"stages": list(vocab.ROLE_STAGES[r]), "skill": f"holon-{r}", "function": ROLE_FUNCTION[r]}
         for r in vocab.ROLES}


# optional pipeline stages: auto = the bylaws rules decide per work node; always; never
PIPELINE_DEFAULTS = {"research": "auto", "shape": "auto", "retro": "auto"}


class BylawsError(ValueError):
    pass


def stages_for(roles: list[str]) -> list[str]:
    bad = set(roles) - set(ROLES)
    if bad:
        raise BylawsError(f"unknown role(s) {sorted(bad)}; roles are {list(ROLES)}")
    return [s for s in vocab.STAGES if vocab.STAGE_ROLE[s] in roles]


def profile_paths(home: Home | None) -> dict[str, Path | object]:
    found: dict = {p.name[:-5]: p for p in resources.files("holon.bylaws").iterdir() if p.name.endswith(".toml")}
    if home and (home.root / "bylaws").is_dir():
        found.update({p.stem: p for p in (home.root / "bylaws").glob("*.toml")})
    return found


def load_profile(ref: str, home: Home | None = None) -> dict:
    """A profile name (stdlib or instance) or a path to a TOML file."""
    path = Path(ref).expanduser()
    if ref.endswith(".toml") and path.exists():
        text = path.read_text()
    else:
        paths = profile_paths(home)
        if ref not in paths:
            raise BylawsError(f"no bylaws profile {ref!r}; available: {sorted(paths)}")
        text = paths[ref].read_text()
    return validate(tomllib.loads(text))


def validate(g: dict) -> dict:
    slots = g.get("slots") or []
    if not g.get("name") or not slots:
        raise BylawsError("a profile needs a name and at least one [[slots]] entry")
    names, covered = set(), set()
    for s in slots:
        if not s.get("name") or not s.get("roles"):
            raise BylawsError("every slot needs a name and roles")
        if s["name"] in names:
            raise BylawsError(f"duplicate slot {s['name']}")
        names.add(s["name"])
        stages_for(s["roles"])
        covered |= set(s["roles"])
        if {"executor", "verifier"} <= set(s["roles"]):
            raise BylawsError(f"slot {s['name']}: one slot may not both execute and verify")
        s.setdefault("min", 1)
    missing = set(ROLES) - covered - ({"executor"} if g.get("delegates_execution") else set())
    if missing:
        raise BylawsError(f"profile {g['name']} leaves roles unfilled: {sorted(missing)}")
    pipeline = dict(g.get("pipeline") or {})  # only what the profile sets; the rest comes from holon.toml
    for k, v in pipeline.items():
        if k not in PIPELINE_DEFAULTS or v not in ("auto", "always", "never"):
            raise BylawsError(f"[pipeline] {k} = {v!r}: keys {sorted(PIPELINE_DEFAULTS)}, values auto | always | never")
    return {"name": g["name"], "version": int(g.get("version", 1)), "description": g.get("description", ""),
            "slots": slots, "delegates_execution": bool(g.get("delegates_execution")), "pipeline": pipeline}


def staffing(bylaws: dict, employees: list[dict]) -> dict:
    """Which slots are filled, by whom, and which separation rules the current staff breaks."""
    out = []
    for s in bylaws["slots"]:
        holders = [e for e in employees if set(s["roles"]) <= set(e["roles"])]
        out.append({"slot": s["name"], "roles": s["roles"], "min": s["min"],
                    "filled_by": [f"{e['name']} ({e['id']})" for e in holders], "ok": len(holders) >= s["min"]})
    warnings = []
    executors = {e["id"] for e in employees if "executor" in e["roles"]}
    verifiers = {e["id"] for e in employees if "verifier" in e["roles"]}
    if executors and (not verifiers or (len(executors) == 1 and verifiers <= executors)):
        warnings.append("no verifier independent of the executor: results wait for a verifier from another department")
    return {"profile": bylaws["name"], "slots": out, "complete": all(s["ok"] for s in out), "warnings": warnings}
