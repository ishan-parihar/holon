"""Plan grades: G0 routine · G1 minor · G2 significant · G3 major · G4 critical/irreversible.

effective grade = max(planner's proposed grade, kernel floor). The floor comes only from
objective signals, so an employee cannot grade its way past the Chair (the Goodhart guard);
the Chair can always regrade.
"""

from __future__ import annotations

import fnmatch
import sqlite3
from pathlib import Path

GRADE_NAMES = {0: "routine", 1: "minor", 2: "significant", 3: "major", 4: "critical"}
RISK_FLOOR = {"low": 0, "normal": 1, "high": 3, "irreversible": 4}
DEFAULTS = {"novelty_min_verified": 3, "default_proposed": 2, "sensitive_paths": []}


def parse_grade(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().upper().lstrip("G")
    try:
        g = int(v)
    except (TypeError, ValueError):
        return None
    return g if 0 <= g <= 4 else None


def verified_in_class(conn: sqlite3.Connection, task_class: str | None) -> int:
    if not task_class:
        return 0
    return conn.execute(
        "SELECT count(DISTINCT t.id) FROM tasks t JOIN runs u ON u.task_id=t.id AND u.kind='execute' "
        "JOIN results r ON r.run_id=u.id WHERE t.task_class=? AND r.verified=1", (task_class,)).fetchone()[0]


def grade_plan(conn: sqlite3.Connection, task: dict, env: dict, cfg: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **((cfg or {}).get("grading", {}))}
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
    for w in env.get("writes") or []:
        if not isinstance(w, str) or "://" in w:
            continue
        p = Path(w).expanduser()
        if p.is_absolute() and (workdir is None or not p.resolve().is_relative_to(workdir)):
            bump(2, f"writes outside the task workdir ({w}): floor G2")
        for pattern in cfg["sensitive_paths"]:
            if fnmatch.fnmatch(str(p), pattern) or fnmatch.fnmatch(w, pattern):
                bump(3, f"writes a sensitive path ({w} ~ {pattern}): floor G3")
    if task.get("task_class") and verified_in_class(conn, task["task_class"]) < int(cfg["novelty_min_verified"]):
        bump(2, f"task class '{task['task_class']}' has fewer than {cfg['novelty_min_verified']} verified "
                "successes (novel work): floor G2")
    elif not task.get("task_class"):
        bump(2, "unclassified task (no track record to trust): floor G2")
    proposed = parse_grade(env.get("grade"))
    effective = max(floor, proposed if proposed is not None else int(cfg["default_proposed"]))
    if proposed is not None and proposed < floor:
        reasons.append(f"planner proposed G{proposed}, raised to the floor")
    return {"proposed": proposed, "floor": floor, "grade": effective, "reasons": reasons}
