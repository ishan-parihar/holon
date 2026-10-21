"""`holon adapter test <driver>`: does a harness honor the contract (docs/CONTRACT.md)?

Runs the driver through a real pipeline in a scratch organization (never the caller's), so a
harness can be checked before any employee is hired onto it. Clauses:

  driver    the driver loads, reaches the brief, and serves every stage
  plan      an observe stage returns a valid plan and leaves the working directory untouched
  approve   an observe stage returns a valid decision for a plan routed to its employee
  execute   the act stage does the approved work and returns a valid result
  verify    an observe stage judges the result; the work closes

Real harnesses spend a few model calls. The scratch instance is deleted afterwards unless a
working directory is given.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from holon.core import vocab
from holon.core.paths import Home

CLAUSES = ("driver", "plan", "approve", "execute", "verify")
TARGET = "conformance.txt"
JD = ("Conformance employee: checks that this harness honors the Holon contract. Do exactly what the "
      "brief asks and return the JSON result it describes.")


def run(k, driver_name: str, workdir: str | None = None, clauses: list[str] | None = None) -> dict:
    import time as _t
    from holon.execution import drivers
    from holon.execution.service import Kernel

    wanted = clauses or list(CLAUSES)
    bad = set(wanted) - set(CLAUSES)
    if bad:
        raise ValueError(f"unknown clauses {sorted(bad)}; clauses: {CLAUSES}")
    results: list[dict] = []
    state: dict = {}

    def clause(name: str, fn) -> None:
        if name not in wanted:
            return
        try:
            ok, detail = fn()
        except Exception as e:  # a clause that crashes is a failed clause, not a crashed suite
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append({"clause": name, "ok": ok, "detail": detail})

    drv = drivers.get(k.home, driver_name)
    scratch = Path(tempfile.mkdtemp(prefix="holon-conformance-"))
    work = Path(workdir).expanduser().resolve() if workdir else scratch / "work"
    work.mkdir(parents=True, exist_ok=True)
    home = Home(scratch / "home")
    home.root.mkdir(parents=True)
    home.drivers_dir.mkdir(parents=True)
    if not drv.source.startswith("builtin:"):
        shutil.copy2(drv.source, home.drivers_dir / Path(drv.source).name)
    home.config_file.write_text('[pipeline]\nresearch = "never"\nshape = "never"\nretro = "never"\n'
                                "[grading]\nnovelty_min_verified = 0\n"
                                "[planner]\nenforce_read_only = true\ntrust_harness_read_only = false\n")
    s = Kernel(home)
    try:
        def check_driver():
            refs = [p for p in ("{prompt}", "{briefing}", "{brief_md}", "{spawn}") if p in drv.command]
            missing = [st for st in vocab.STAGES if st not in drv.stages]
            if missing:
                return False, f"does not serve stages {missing}"
            how = ", ".join(refs) if refs else "$HOLON_BRIEF_DIR only (the plan clause shows whether it reads it)"
            return True, f"reaches the brief via {how}; serves every stage"
        clause("driver", check_driver)

        o = s.org
        ceo = o.hire(name="Conformance CEO", title="CEO", dept=vocab.EXECUTIVE, job_description=JD, slot="ceo",
                     driver=driver_name, reason="conformance", status="active")["id"]
        o.create_dept("conformance", "Conformance", "Check one harness against the Holon contract")
        lead = o.hire(name="Conformance Lead", title="Head of Conformance", dept="conformance", job_description=JD,
                      roles=["head", "planner", "verifier"], driver=driver_name, reason="conformance",
                      status="active")["id"]
        maker = o.hire(name="Conformance Maker", title="Executor", dept="conformance", job_description=JD,
                       roles=["executor"], driver=driver_name, reason="conformance", status="active")["id"]
        o.set_authority(f"emp:{ceo}", "*", 4, "conformance: the CEO approves everything here")
        w = s.work.add_work(f"Create {TARGET}", f"Create a file named {TARGET} in the working directory {work} "
                            "containing exactly the word ok.", dept="conformance", task_class="conformance",
                            risk="low", workdir=str(work), acceptance=f"{TARGET} exists and contains ok",
                            assignee=None)
        state["work"] = w["id"]

        def check_plan():
            out = s.dispatch(lead, "plan")
            if out is None:
                return False, "no plan stage was claimable"
            if out.get("breach"):
                return False, "; ".join(out["breach"])
            a = None
            for _ in range(20):  # the same self-submit race: poll briefly for the approve stage
                a = s.work.latest(state["work"], "approve")
                if a is not None:
                    break
                _t.sleep(0.5)
            if a is None:
                return False, f"no approval stage after the plan (work {s.work.get(state['work'])['status']})"
            return True, f"valid plan, graded G{a['grade']}, working directory untouched"
        clause("plan", check_plan)

        def check_approve():
            out = s.dispatch(ceo, "approve")
            if out is None:
                return False, "no approval was routed to the CEO (did the plan clause pass?)"
            if out.get("breach"):
                return False, "; ".join(out["breach"])
            # A body that submits itself (pull-in-push) does so from the harness's own MCP child;
            # the commit lands a beat after run_driver returns (measured 2026-10-21: the harness
            # was stopped at 04:11:5X, the decision committed at 04:11:55). Poll briefly rather
            # than reading once — a real decision is never more than a few seconds behind.
            a = None
            for _ in range(20):  # bounded: ~10s
                a = s.work.latest(state["work"], "approve", "done")
                if a is not None:
                    break
                _t.sleep(0.5)
            if a is None:
                return False, "the approval was not decided"
            return True, f"decision: {a['outcome']} ({a.get('decision_note') or ''})"[:200]
        clause("approve", check_approve)

        def check_execute():
            out = s.dispatch(maker, "execute")
            if out is None:
                return False, "no execute stage was claimable (was the plan approved?)"
            if out.get("breach"):
                return False, "; ".join(out["breach"])
            target = work / TARGET
            ex = None
            for _ in range(20):  # the same self-submit race as approve: poll briefly
                ex = s.work.latest(state["work"], "execute")
                if ex is not None:
                    break
                _t.sleep(0.5)
            outcome = ((s.work.output(ex["id"]) if ex else None) or {"body": {}})["body"].get("outcome")
            if not target.exists() or "ok" not in target.read_text().lower():
                return False, f"result {outcome} but {TARGET} is missing or wrong"
            return True, f"{TARGET} written; outcome {outcome}"
        clause("execute", check_execute)

        def check_verify():
            out = s.dispatch(lead, "verify")
            if out is None:
                return False, "no verify stage was claimable"
            if out.get("breach"):
                return False, "; ".join(out["breach"])
            status = s.work.get(state["work"])["status"]
            v = None
            for _ in range(20):  # the same self-submit race: poll briefly
                v = s.work.latest(state["work"], "verify", "done")
                if v is not None or status == "done":
                    break
                status = s.work.get(state["work"])["status"]
                _t.sleep(0.5)
            return status == "done", f"verdict {v['outcome'] if v else None}; work {status}"
        clause("verify", check_verify)
    finally:
        s.close()
        if not workdir:
            shutil.rmtree(scratch, ignore_errors=True)
    return {"driver": driver_name, "clauses": results, "passed": sum(1 for r in results if r["ok"])}
