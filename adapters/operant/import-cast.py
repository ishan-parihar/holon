#!/usr/bin/env python3
"""Import Operant's cast into Holon (H9, phase G).

The nine seeded seats (operant's `employees` table) map onto Holon departments, roles and
kernel mechanisms per ORG-LAYER-DISPOSITION.md §5, and each cast cron job is re-proposed as
a routine through the classifier (`holon routine add` — approval follows the grade). The
importer is here, not in the kernel CLI, because the kernel names no harness (law 2).

    python3 adapters/operant/import-cast.py                                   # dry run
    python3 adapters/operant/import-cast.py --apply                            # hire + propose
    python3 adapters/operant/import-cast.py --org-db <db> --cron-db <db>       # non-default paths

    python3 adapters/operant/import-cast.py --holon-home ~/holon --driver operant --apply

The mapping (seat → Holon home):
- premiere              the CEO and the Chair's channel; its daily directive is an executive
                        routine with --deliver (no hire — the CEO exists per instance)
- chief-of-staff        an executive employee (planner, steward roles); its daily brief is an
                        executive routine with --deliver
- governor              the executive's audit duty: a governance-digest routine (doctor and
                        lint triage, audit samples, retros are kernel commands)
- identity-warden       an identity-audit routine in the department that owns identity (it)
- compass               an executive employee (planner) reading the goals tree; weekly
                        strategy-review routine
- crew-chief, hrmaster,
  dispatcher           kernel mechanisms (metrics, hr review, the board): nothing to apply;
  dp-the-program        the run supervisor (H6) and each department's executor slots
Cron rows that are not `cron_cast_*` are listed but not proposed: from phase G on,
`operant cron add` proposes routines itself.

Operant's databases are opened read-only; the org tables are archived, never migrated.
Schedules drop their leading seconds field (operant is 6-field; Holon takes 5).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from holon.core.paths import Home                                   # noqa: E402
from holon.execution.cron import Cron                               # noqa: E402
from holon.execution.service import Kernel                          # noqa: E402

# seat → (dept for its routines, hire?, slot, roles, deliver)
HIRES = {  # seats that become Holon employees (slot names are the executive profile's)
    "chief-of-staff": ("executive", "strategy", ["planner", "steward"]),
    "compass": ("executive", "strategy", ["planner"]),
}
ROUTINES = {  # seat → (dept, deliver); the cron's own name is the routine's title
    "premiere": ("executive", True),
    "chief-of-staff": ("executive", True),
    "governor": ("executive", False),
    "identity-warden": ("it", False),
    "compass": ("executive", False),
}
MECHANISMS = {  # seat → the kernel mechanism that covers its duty
    "crew-chief": "`holon metrics` and `holon doctor` (telemetry and health)",
    "hrmaster": "`holon hr review`, probation cadence, earned authority",
    "dispatcher": "the board (`holon status`), claims and the concurrency cap",
    "dp-the-program": "the run supervisor (CONTRACT §12) and the departments' executor slots",
}


def holon_cron(schedule: str, shift_min: int = 0) -> str:
    """Operant's 6-field schedule (seconds first) as Holon's 5-field cron. `shift_min` staggers a
    daily cadence (H-11): four routines proposed for the same minute would otherwise fire as one burst."""
    five = " ".join(schedule.split()[1:])
    f = five.split()
    if shift_min and len(f) == 5 and f[1].isdigit() and f[2:] == ["*", "*", "*"]:
        f[0] = str((int(f[0]) + shift_min) % 60)
        five = " ".join(f)
    Cron(five)
    return five


def fires_per_day(cron: str) -> int:
    """A rough lower bound of a 5-field cron's firings a day — enough to spot an hourly agent job."""
    f = cron.split()
    if len(f) != 5:
        return 0
    if f[1] == "*":
        return 24
    if "," in f[1] or "-" in f[1] or f[1].startswith("*/"):
        return 12
    return 1


def read_org(org_db: Path, cron_db: Path) -> tuple[list[dict], list[dict]]:
    """The live cast and its cron jobs, read-only."""
    def rows(db: Path, table: str) -> list[dict]:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
        finally:
            conn.close()
    return (rows(org_db, "employees"), rows(cron_db, "cron_jobs"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holon-home", default=str(Path("~/holon").expanduser()))
    ap.add_argument("--org-db", default=str(Path("~/.operant/database.db").expanduser()),
                    help="operant's org database (the employees table)")
    ap.add_argument("--cron-db", default=str(Path("~/.operant/operant_cron.db").expanduser()),
                    help="operant's cron database (the cron_jobs table)")
    ap.add_argument("--driver", default="operant", help="the harness the imported employees run in")
    ap.add_argument("--dept-map", default=[], action="append", metavar="seat=slug",
                    help="override where a seat lands (repeatable), e.g. identity-warden=ops")
    ap.add_argument("--stagger", type=int, default=7,
                    help="minutes between proposed daily routines (H-11: same-minute jobs are one burst otherwise)")
    ap.add_argument("--apply", action="store_true", help="hire and propose (default: dry run)")
    a = ap.parse_args(argv)
    dept_map = dict(kv.split("=", 1) for kv in a.dept_map)

    seats, crons = read_org(Path(a.org_db), Path(a.cron_db))
    k = Kernel(Home(Path(a.holon_home).expanduser()))
    try:
        hires = {sid: (dept_map.get(sid, d), slot, roles) for sid, (d, slot, roles) in HIRES.items()}
        routines = {sid: (dept_map.get(sid, d), deliver) for sid, (d, deliver) in ROUTINES.items()}
        present = {s["employee_id"] for s in seats}
        needed = sorted(({hires[s][0] for s in present if s in hires}
                         | {routines[s][0] for s in present if s in routines}))
        known = {d["slug"] for d in k.org.depts()}
        missing = [d for d in needed if d not in known]
        if missing:
            print("missing departments: " + ", ".join(missing)
                  + "\n  create them first (holon dept create), or pass --dept-map seat=slug")
            return 1
        cast = [c for c in crons if c["id"].startswith("cron_cast_")]
        other = [c for c in crons if not c["id"].startswith("cron_cast_")]
        print(f"{len(seats)} seats, {len(cast)} cast crons, {len(other)} other cron jobs\n")
        applied = []
        stagger = 0
        for s in seats:
            sid = s["employee_id"]
            cron = next((c for c in cast if c["id"] == f"cron_cast_{sid}"), None)
            if sid in hires:
                dept, slot, roles = hires[sid]
                print(f"hire      {sid:<18} executive employee (slot {slot}, roles {'+'.join(roles)})")
                if a.apply:
                    emp = k.org.hire(name=s["name"], title=s["name"].title(), dept=dept,
                                     job_description=s["system_prompt"] or s["role"], driver=a.driver,
                                     slot=slot, roles=roles, reason=f"operant cast import ({sid})")
                    applied.append(f"hired {s['name']} ({emp['id']}) into {dept}")
            if sid in routines:
                if cron is None:
                    print(f"routine?  {sid:<18} no cron_cast row: nothing to propose")
                else:
                    dept, deliver = routines[sid]
                    when = holon_cron(cron["schedule"], shift_min=stagger * a.stagger)
                    hot = fires_per_day(when) >= 12
                    print(f"routine   {sid:<18} dept {dept}: '{cron['name']}' on {when}"
                          + (" (delivered)" if deliver else "")
                          + ("\n          ⚠ agent cadence ≥ 12×/day: review before approving (script lane? slower cron?)"
                             if hot else ""))
                    stagger += 1
                    if a.apply:
                        approval = k.add_routine(cron["name"], cron["prompt"][:2000], dept=dept,
                                                 cron=when, deliver=deliver,
                                                 task_class=f"operant-cast:{sid}")
                        applied.append(f"routine proposal for {sid} → approval {approval['id']}")
            elif sid in MECHANISMS:
                print(f"mechanism {sid:<18} {MECHANISMS[sid]}")
        for c in other:
            print(f"other     {c['id']:<18} '{c['name']}' on {c['schedule']} — not a cast duty; "
                  "`operant cron add` proposes routines from phase G on")
        print("\ndry run (nothing applied); pass --apply to hire and propose" if not a.apply
              else "\napplied:\n  " + "\n  ".join(applied or ["nothing to apply"]))
    finally:
        k.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
