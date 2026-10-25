"""The operationalization gates (2026-10-25 red-team).

Measured on a real imported instance: the Chair-approved ventures import landed 26 routine
proposals in three departments with **zero employees**, and every operationalization seam
accepted the state without a word:

- `schedule enable` lit an unstaffed department's recurring schedule with no resistance (F1);
- `add_routine`'s grade reasons — the only thing the approver reads — said nothing about
  staffing (F2);
- doctor reported nothing at all: no backstop existed, on any surface (F3);
- `hr hire --driver` accepted a driver name nothing configures (F4);
- a `deliver` routine on an instance with no `[notify]` command silently drops results (F5);
- a script routine whose workdir is gone silently ran in the run directory instead (F6);
- forced end to end, the firing's stage sat `ready` in a department where nobody could claim
  it (F7) — the strand every one of the above permits.

An agent building a system inside Holon (a department, its routines, its staffing) must not
be able to create something that cannot operate without the surface it speaks through naming
what is missing. The gates here are fail-closed at every seam, with the Chair's escape where
the gate's class demands one (the ledger's own precedent: `isolation apply --force`).
"""

import json
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import KernelCase, JD
from tests.test_lanes import LaneCase


def staffed_lane_stages(lane: str) -> tuple[str, ...]:
    """The stages a lane's firing needs somebody (besides the kernel) to perform."""
    return {"script": (), "fast": ("execute",), "full": ("plan", "execute", "verify")}[lane]


class OperationalGates(LaneCase):
    """One gate per seam. The fixture's `eng` department starts staffed; the tests fire each
    gate by hiring nothing into a fresh department."""

    def _unstaffed_routine(self, lane: str, title="Ghost digest", intent_extra=None, **kw):
        """A routine in a department with no employees, in the given lane."""
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        with mock.patch.object(self.k, "classify", return_value={"lane": lane, "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine(title, intent_extra or "summarize the ghost pool", dept="ghost",
                                   cron="0 9 * * *", workdir=str(self.workdir), **kw)
        return a

    def _approval_id(self, a: dict) -> str:
        return self.k.work.get(a["id"])["id"] and a["id"]

    # ── F1/F7: the enable seam — an unstaffed schedule must not light ───────────────
    def _parked_import(self, intent="summarize the ghost pool"):
        """An imported, outside-reach routine in the unstaffed dept: parks OFF on approval, so the
        enable gate is the seam its schedule reaches (a hand-written one is refused at approval)."""
        with mock.patch.object(self.k, "_external_by_name", return_value=(True, "send")):
            b = self._unstaffed_routine("fast", title="Ghost outreach", intent_extra=intent,
                                        imported=True, why="agent-shaped")
        self.k.approve(b["id"])
        plan = self.k.work.plan_for_approval(b["id"])
        return self.k.wconn.execute("SELECT id FROM schedules WHERE template_id=?", (plan["id"],)).fetchone()[0]

    def test_schedule_enable_refuses_an_unstaffed_lane(self):
        sid = self._parked_import()
        with self.assertRaises(ValueError) as cm:
            self.k.schedules.set_enabled(sid, True)
        self.assertIn("no employee in ghost performs execute", str(cm.exception))
        self.assertIn("holon hr hire", str(cm.exception),
                      "the refusal names the act that fixes it")
        self.assertFalse(self.k.schedules.get(sid)["enabled"], "the refusal left it OFF")

    def test_the_enable_gate_passes_a_staffed_lane(self):
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        self.k.org.hire(name="Gee", title="maker", dept="ghost", job_description=JD, slot="maker",
                        driver="fake", reason="staffing", status="active")
        with mock.patch.object(self.k, "classify", return_value={"lane": "fast", "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost digest", "summarize the ghost pool", dept="ghost",
                                   cron="0 9 * * *", workdir=str(self.workdir))
        self.k.approve(a["id"])
        plan = self.k.work.plan_for_approval(a["id"])
        sid = self.k.wconn.execute("SELECT id FROM schedules WHERE template_id=?", (plan["id"],)).fetchone()[0]
        s = self.k.schedules.set_enabled(sid, True)   # staffed: no refusal
        self.assertTrue(s["enabled"])

    def test_the_enable_gate_ignores_script_lanes(self):
        """The kernel is the script lane's runner: no employee is ever needed."""
        a = self._unstaffed_routine("script", title="Ghost script", command="true")
        self.k.approve(a["id"])
        plan = self.k.work.plan_for_approval(a["id"])
        sid = self.k.wconn.execute("SELECT id FROM schedules WHERE template_id=?", (plan["id"],)).fetchone()[0]
        self.assertTrue(self.k.schedules.get(sid)["enabled"],
                        "a script routine in an unstaffed dept starts on approval, as before")
        s = self.k.schedules.set_enabled(sid, True)   # and re-enable is fine too
        self.assertTrue(s["enabled"])

    def test_the_chair_may_force_an_unstaffed_enable(self):
        """The gate's escape: the Chair's explicit word, recorded in the event log."""
        sid = self._parked_import()
        s = self.k.schedules.set_enabled(sid, True, force=True)
        self.assertTrue(s["enabled"], "the Chair's --force lights it, named in the event log")
        ev = self.k.wconn.execute("SELECT note FROM events WHERE entity='schedule' AND entity_id=? "
                                  "ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
        self.assertIn("unstaffed", (ev[0] or ""), "the forced enable says why it needed forcing")

    # ── F2: the proposal seam — the approver reads the staffing note ──────────────
    def test_the_grade_reasons_name_the_missing_staff(self):
        a = self._unstaffed_routine("fast")
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        self.assertTrue(any("no employee in ghost performs execute" in r for r in reasons),
                        f"the approver reads: {reasons}")
        self.assertTrue(any("holon hr hire" in r for r in reasons),
                        "the note names the act that fixes it")

    def test_the_staffed_proposal_carries_no_staffing_note(self):
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        self.k.org.hire(name="Gee", title="maker", dept="ghost", job_description=JD, slot="maker",
                        driver="fake", reason="staffing", status="active")
        with mock.patch.object(self.k, "classify", return_value={"lane": "fast", "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost digest", "summarize the ghost pool", dept="ghost",
                                   cron="0 9 * * *", workdir=str(self.workdir))
        plan = self.k.work.get(a["id"])
        reasons = json.loads(plan["grade_reasons"] or "[]")
        self.assertFalse(any("performs" in r and "no employee" in r for r in reasons),
                        f"a staffed department carries no false note: {reasons}")

    # ── the approval seam: a starts-on-approval routine in an unstaffed dept ───────
    def test_an_unstaffed_starts_on_approval_routine_refuses_to_apply(self):
        """A hand-written (not imported) internal routine starts on approval. Approving one in an
        unstaffed department must refuse the apply — the approval transaction aborts, the work is
        not finished — rather than light a schedule nothing can run."""
        a = self._unstaffed_routine("fast")     # not imported: no starts_disabled
        with self.assertRaises(ValueError) as cm:
            self.k.approve(a["id"])
        self.assertIn("no employee in ghost performs execute", str(cm.exception))
        self.assertEqual(self.k.work.get(a["id"])["status"], "ready",
                         "the refused apply leaves the proposal pending, not half-applied")

    def test_the_imports_off_parking_is_not_gated(self):
        """An imported routine lands OFF (I1): the parking state is the design, so approving it
        must succeed — the gate lives at enable, not at the import's parking approval."""
        sid = self._parked_import()
        self.assertFalse(self.k.schedules.get(sid)["enabled"],
                         "the imported unstaffed routine parks OFF, approved — enable is the gate")

    # ── F3: the backstop — doctor reports an enabled unstaffed schedule ───────────
    def test_doctor_reports_an_enabled_unstaffed_schedule(self):
        from holon.integrity import doctor
        sid = self._parked_import()
        self.k.schedules.set_enabled(sid, True, force=True)
        found = {f["check"]: f for f in doctor.run(self.k)}
        self.assertIn("schedule.unstaffed", found, f"doctor must report it: {sorted(found)}")
        self.assertEqual(found["schedule.unstaffed"]["severity"], "error",
                         "the heartbeat turns an error into an incident")
        self.assertIn("hr hire", found["schedule.unstaffed"]["message"])

    # ── F4: the hire seam — a driver nothing configures must refuse at hire ────────
    def test_hire_refuses_a_driver_nothing_configures(self):
        from holon.execution import drivers
        self.assertNotIn("nonexistent-driver", drivers.available(self.k.home))
        with self.assertRaises(Exception) as cm:
            self.k.org.hire(name="Hex", title="maker", dept="eng", job_description=JD, slot="maker",
                            driver="nonexistent-driver", reason="test")
        self.assertIn("nonexistent-driver", str(cm.exception))

    # ── F5: the delivery seam — deliver with no channel is named at add time ──────
    def test_a_deliver_routine_names_its_missing_channel(self):
        self.configure("")        # no [notify] block: nothing configured
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        with mock.patch.object(self.k, "classify", return_value={"lane": "fast", "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost digest", "summarize the ghost pool", dept="ghost",
                                   cron="0 9 * * *", workdir=str(self.workdir), deliver=True)
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        self.assertTrue(any("[notify]" in r and "deliver" in r for r in reasons),
                        f"the approver reads that results will reach nobody: {reasons}")

    # ── F6: the script seam — an enabled script schedule with a dead workdir ───────
    def test_doctor_reports_an_enabled_script_schedule_with_a_dead_workdir(self):
        from holon.integrity import doctor
        a = self._unstaffed_routine("script", title="Ghost script", command="true")
        self.k.approve(a["id"])
        import shutil
        shutil.rmtree(self.workdir)
        found = {f["check"]: f for f in doctor.run(self.k)}
        self.assertIn("schedule.workdir", found,
                      "an enabled script schedule whose workdir is gone is visible before it runs wrong")

    # ── the predicate itself: what each lane needs ────────────────────────────────
    def test_the_lane_stages_predicate(self):
        self.assertEqual(staffed_lane_stages("script"), ())
        self.assertEqual(staffed_lane_stages("fast"), ("execute",))
        self.assertEqual(staffed_lane_stages("full"), ("plan", "execute", "verify"))


class StaffingReach(KernelCase):
    """The staffing predicate must mirror the claim's reach, or it gates the wrong things. The
    claim's pool branch (`work.claim`): a claimant may pick up work in their OWN department, and
    an executive claimant additionally reaches no-dept work and verify stages of any department —
    but NOT another department's plan or execute stages. So a full-lane routine in an unstaffed
    department is missing plan too, even with a CEO in executive (who reaches only verify)."""

    def test_executive_reach_covers_only_what_the_claim_covers(self):
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        with mock.patch.object(self.k, "classify", return_value={"lane": None, "confidence": 0.0,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost full", "the whole pipeline", dept="ghost", cron="0 9 * * *",
                                   workdir=str(self.workdir), why="needs the pipeline")
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        note = next(r for r in reasons if "unstaffed for its lane" in r)
        for stage in ("execute", "plan"):
            self.assertIn(stage, note, f"{stage} is not the executive's to claim for another department")
        self.assertNotIn("verify", note, "the CEO reaches verify stages anywhere (executive reach)")

    def test_a_dept_employee_covers_their_own_lanes(self):
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        self.k.org.hire(name="Gee", title="maker", dept="ghost", job_description=JD, slot="maker",
                        driver="fake", reason="staffing", status="active")
        with mock.patch.object(self.k, "classify", return_value={"lane": "fast", "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost digest", "summarize", dept="ghost", cron="0 9 * * *",
                                   workdir=str(self.workdir))
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        self.assertFalse(any("unstaffed for its lane" in r for r in reasons),
                        f"a maker in the dept covers fast: {reasons}")


if __name__ == "__main__":
    unittest.main()
