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
    "dp-the-program": "the run supervisor (CONTRACT §11) and the departments' executor slots",
}


def holon_cron(schedule: str) -> str:
    five = " ".join(schedule.split()[1:])  # operant schedules are 6-field (seconds first)
    Cron(five)
    return five


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
    ap.add_argument("--apply", action="store_true", help="hire and propose (default: dry run)")
    a = ap.parse_args(argv)

    seats, crons = read_org(Path(a.org_db), Path(a.cron_db))
    k = Kernel(Home(Path(a.holon_home).expanduser()))
    try:
        cast = [c for c in crons if c["id"].startswith("cron_cast_")]
        other = [c for c in crons if not c["id"].startswith("cron_cast_")]
        print(f"{len(seats)} seats, {len(cast)} cast crons, {len(other)} other cron jobs\n")
        applied = []
        for s in seats:
            sid = s["employee_id"]
            cron = next((c for c in cast if c["id"] == f"cron_cast_{sid}"), None)
            if sid in HIRES:
                dept, slot, roles = HIRES[sid]
                print(f"hire      {sid:<18} executive employee (slot {slot}, roles {'+'.join(roles)})")
                if a.apply:
                    emp = k.org.hire(name=s["name"], title=s["name"].title(), dept=dept,
                                     job_description=s["system_prompt"] or s["role"], driver=a.driver,
                                     slot=slot, roles=roles, reason=f"operant cast import ({sid})")
                    applied.append(f"hired {s['name']} ({emp['id']}) into {dept}")
            if sid in ROUTINES:
                if cron is None:
                    print(f"routine?  {sid:<18} no cron_cast row: nothing to propose")
                else:
                    dept, deliver = ROUTINES[sid]
                    print(f"routine   {sid:<18} dept {dept}: '{cron['name']}' on {holon_cron(cron['schedule'])}"
                          + (" (delivered)" if deliver else ""))
                    if a.apply:
                        approval = k.add_routine(cron["name"], cron["prompt"][:2000], dept=dept,
                                                 cron=holon_cron(cron["schedule"]), deliver=deliver,
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
