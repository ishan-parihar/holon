"""The supersede remedy (2026-10-22 readiness audit): doctor's `artifacts.dangling` names a
command that did not exist ("supersede the result") — the organization's record pointed at
work whose artifacts rotted, and the only remedies were restoring a /var/tmp scratchpad
rotted by 30-day cleaning or writing SQL by hand. A result whose record no longer resolves
can now be withdrawn by the Chair: the output row is marked superseded (migration 0018),
doctor stops reporting it, and memory's artifact pins for that work are retired by memory.py,
the only writer of memory.db (law 3). The work's own status is not rewritten — history is
history; only the record's standing changes."""
import unittest

from holon.integrity import doctor
from holon.execution.work import TransitionError
from tests.helpers import KernelCase


def _cli_path(cmd):
    """The registry's declared actor class for a command path, or None."""
    from holon.cli import manifest
    return next((c["actor"] for c in manifest() if c["command"] == cmd), None)


class ASupersededResultIsWithdrawn(KernelCase):
    def _executed(self, artifacts, outcome="success"):
        w = self.add("Build report", dept="eng", task_class="ops", risk="low")
        self.plan_and_approve(w, planner=self.hod, assignee=self.dev1,
                              writes=[str(self.workdir / "rpt.md")])
        c = self.k.claim("execute", self.dev1, task_id=self.k.work.latest(w["id"], "execute")["id"])
        return w, self.k.submit(c.run["id"], self.exec_env(outcome=outcome, artifacts=artifacts))

    def _pins(self, run_id):
        import sqlite3
        db = sqlite3.connect(f"file:{self.k.home.memory_db}?mode=ro", uri=True)
        out = db.execute("SELECT content, superseded_by FROM items WHERE kind='artifact' AND provenance LIKE ?",
                         (f"%{run_id}%",)).fetchall()
        db.close()
        return out

    def _dangling(self):
        return [f for f in doctor.run(self.k) if f["check"] == "artifacts.dangling"]

    def test_supersede_marks_the_output_and_records_the_event(self):
        """The remedy doctor names is real: `task supersede` marks the execute output
        superseded (the migration's column), records an audit event on the execute stage, and
        leaves the work's own status untouched — the result is withdrawn, not the history."""
        w, out = self._executed(artifacts=["/nonexistent/rotted-path.txt"])
        self.assertTrue(self._dangling(), "a rotted artifact must dangle before the remedy runs")
        before = self.k.work.get(w["id"])["status"]
        res = self.k.work.supersede(w["id"], "artifact rotted in /var/tmp")
        for run_id in res["runs"]:
            self.k.memory.retire_artifacts(run_id)  # what the CLI does with the returned ids
        ex = self.k.work.stages(w["id"], "execute")[-1]
        self.assertEqual(self.k.work.output(ex["id"])["superseded"], 1)
        ev = self.k.wconn.execute("SELECT note FROM events WHERE entity_id=? ORDER BY seq DESC LIMIT 1",
                                  (ex["id"],)).fetchone()
        self.assertTrue(ev and "rotted" in ev[0], "the supersede is an audit event")
        self.assertEqual(self.k.work.get(w["id"])["status"], before)  # history is history

    def test_supersede_retires_the_memory_pins(self):
        """The pins the failed pointer made must go with it (memory.py is the only writer of
        memory.db): supersede retires every artifact item whose provenance names the run."""
        w, out = self._executed(artifacts=["/nonexistent/rotted.txt"])
        run_id = out["run"]["id"]
        pins = self._pins(run_id)
        self.assertTrue(pins, "the submit pinned an artifact item")
        res = self.k.work.supersede(w["id"], "x")
        for run_id in res["runs"]:
            self.k.memory.retire_artifacts(run_id)  # what the CLI does with the returned ids
        for content, sup in self._pins(out["run"]["id"]):
            self.assertEqual(sup, "retired", f"pin {content!r} not retired")

    def test_doctor_stops_reporting_a_superseded_result(self):
        """The point of the remedy: doctor's `artifacts.dangling` skips superseded outputs, so
        the warning the Chair sees counts only results still standing behind their record."""
        w, out = self._executed(artifacts=["/nonexistent/rotted.txt"])
        self.assertTrue(self._dangling())
        self.k.work.supersede(w["id"], "x")
        self.assertFalse(self._dangling())

    def test_supersede_refuses_work_with_no_execute_result(self):
        """Only a result can be superseded: work that never executed has nothing to withdraw."""
        w = self.add("Nothing yet", dept="eng")
        with self.assertRaises(TransitionError):
            self.k.work.supersede(w["id"], "x")

    def test_supersede_takes_a_work_whose_latest_execute_was_a_cancelled_retry(self):
        """Found on the live instance: a work whose done execute output predates a cancelled
        retry — `latest('execute')` returns the cancelled stage with no output, and supersede
        refused the very work doctor was reporting. A work's execute outputs are walked, not
        the latest stage's."""
        w, out = self._executed(artifacts=["/nonexistent/rotted.txt"])
        # a cancelled retry after the done attempt: the exact live shape
        ex2 = self.k.work._add_stage(self.k.work.get(w["id"]), "execute",
                                     [self.k.work.stages(w["id"], "execute")[-1]["id"]], attempt=2)
        self.k.work._set(ex2["id"], "cancelled", "chair cancelled the retry")
        self.assertTrue(self._dangling())
        res = self.k.work.supersede(w["id"], "x")
        for run_id in res["runs"]:
            self.k.memory.retire_artifacts(run_id)
        self.assertFalse(self._dangling(), "the done attempt's output must be withdrawn too")

    def test_supersede_is_chair_only(self):
        """Withdrawing a finished result is the Chair's: the registry must declare it chair."""
        self.assertEqual(_cli_path("task supersede"), "chair")


if __name__ == "__main__":
    unittest.main()
