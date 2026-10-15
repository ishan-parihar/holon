"""Plan grades: G0 routine · G1 minor · G2 significant · G3 major · G4 critical/irreversible.

effective grade = max(planner's proposed grade, kernel floor). The floor comes only from
objective signals, so an employee cannot grade its way past the Chair (the Goodhart guard);
the Chair can always regrade.
"""

from __future__ import annotations

import fnmatch
import sqlite3
from pathlib import Path

from holon.core.contract import parse_grade
from holon.core.vocab import GRADE_NAMES  # noqa: F401  (re-exported for callers)

RISK_FLOOR = {"low": 0, "normal": 1, "high": 3, "irreversible": 4}
DEFAULTS = {"novelty_min_verified": 3, "default_proposed": 2, "sensitive_paths": []}


def verified_in_class(conn: sqlite3.Connection, task_class: str | None) -> int:
    if not task_class:
        return 0
    return conn.execute(
        "SELECT count(DISTINCT t.parent_id) FROM outputs o JOIN tasks t ON t.id=o.task_id "
        "WHERE o.kind='execute' AND o.verified=1 AND t.task_class=?", (task_class,)).fetchone()[0]


def grade_plan(conn: sqlite3.Connection, task: dict, env: dict, cfg: dict | None = None,
                reach: list[str] | None = None) -> dict:
    """`reach`: the department's scope roots ([scope] in holon.toml). Writes there count as in bounds;
    writes beyond the workdir and the reach of a department that has one go to the Chair (G3).
    In a `[pilot]` department, anything the floor puts at G3 (outside reach) is the Chair's call:
    floor G4 — the standing rule "pilot work never sends outreach" as a grade floor, not a hope."""
    full = cfg or {}
    cfg = {**DEFAULTS, **(full.get("grading", {}))}
    floor, reasons = RISK_FLOOR.get(task.get("risk") or "normal", 1), []
    reasons.append(f"risk={task.get('risk')}: floor G{floor}")

    def bump(g: int, why: str) -> None:
        nonlocal floor
        if g > floor:
            floor = g
            reasons.append(why)

    if env.get("kind") == "decompose":
        bump(2, "decomposition creates new work across the organization: floor G2")
    if env.get("external_effects"):
        bump(2, f"external effects {env['external_effects'][:3]}: floor G2")
    workdir = Path(task["workdir"]).expanduser().resolve() if task.get("workdir") else None
    roots = [Path(r).expanduser().resolve() for r in reach or []]
    for w in env.get("writes") or []:
        if isinstance(w, str) and w.startswith("skill:"):
            bump(2, f"changes how the department works ({w}): floor G2")
            continue
        if not isinstance(w, str) or "://" in w:
            continue
        p = Path(w).expanduser()
        inside = p.is_absolute() and any(p.resolve().is_relative_to(r) for r in ([workdir] if workdir else []) + roots)
        if p.is_absolute() and not inside:
            if roots:
                bump(3, f"writes outside the department's scope ({w}): floor G3")
            else:
                bump(2, f"writes outside the task workdir ({w}): floor G2")
        for pattern in cfg["sensitive_paths"]:
            if fnmatch.fnmatch(str(p), pattern) or fnmatch.fnmatch(w, pattern):
                bump(3, f"writes a sensitive path ({w} ~ {pattern}): floor G3")
    if task.get("task_class") and verified_in_class(conn, task["task_class"]) < int(cfg["novelty_min_verified"]):
        bump(2, f"task class '{task['task_class']}' has fewer than {cfg['novelty_min_verified']} verified "
                "successes (novel work): floor G2")
    elif not task.get("task_class"):
        bump(2, "unclassified task (no track record to trust): floor G2")
    pilot = (full.get("pilot") or {}).get("depts") or []
    if pilot and floor >= 3 and task.get("dept_id"):
        r = conn.execute("SELECT slug FROM departments WHERE id=?", (task["dept_id"],)).fetchone()
        if r and r["slug"] in pilot:
            bump(4, f"pilot department '{r['slug']}': outside-reach (G3) is the Chair's call (G4)")
    proposed = parse_grade(env.get("grade"))
    effective = max(floor, proposed if proposed is not None else int(cfg["default_proposed"]))
    if proposed is not None and proposed < floor:
        reasons.append(f"planner proposed G{proposed}, raised to the floor")
    return {"proposed": proposed, "floor": floor, "grade": effective, "reasons": reasons}
