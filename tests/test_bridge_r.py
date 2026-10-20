"""Tests first for the bridge plan's Layer R — the codebase repairs.

R1: a harness that ends with its envelope in its **final output**, having written no
    `result.json`, must hand that envelope in rather than have its work discarded. The
    brief already permits the path: *"end your final message with that JSON object."*
    Measured cause, both real harnesses: jcode ran 36 tool calls with 0 submits; operant
    ran 66 tool calls and hit its 40-iteration cap with 0. Both were cut with a
    well-formed plan in hand, and both lost it.
"""

import json
import sys
from pathlib import Path

from tests.helpers import KernelCase

REPO = Path(__file__).resolve().parents[1]
FAKE_AGENT = REPO / "tests" / "fake_agent.py"


class BodyHandsInWhatItHas(KernelCase):
    """R1. The kernel salvages the envelope from the body's final output."""

    def install_driver(self, name, mode):
        self.home.drivers_dir.mkdir(parents=True, exist_ok=True)
        (self.home.drivers_dir / f"{name}.toml").write_text(
            f'name = "{name}"\n'
            f'command = "{sys.executable} {FAKE_AGENT}"\n'
            f'timeout_s = 60\nresult = "stdout-json"\n'
            f'[env]\nFAKE_MODE = "{mode}"\nPYTHONPATH = "{REPO}"\n')

    def planner_on(self, name, driver, mode):
        self.install_driver(driver, mode)
        w = self.add(dept="eng")
        emp = self.k.org.hire(name=name, title="Planner", dept="eng", job_description="plan",
                              roles=["planner"], driver=driver, reason="R1", status="active")["id"]
        return emp, w

    def run_of(self, stage_id):
        """The run a stage node's dispatch produced (runs join tasks on task_id)."""
        return next(dict(r) for r in self.k.wconn.execute(
            "SELECT * FROM runs WHERE task_id=? ORDER BY started_at DESC", (stage_id,)))

    def log_of(self, stage_id):
        return (self.home.run_dir(self.run_of(stage_id)["id"]) / "driver.log").read_text()

    def test_a_harness_that_ends_with_the_envelope_hands_it_in(self):
        """The plan reaches the approval queue instead of being discarded."""
        emp, w = self.planner_on("Final Planner", "final", "final-message")
        out = self.k.dispatch(emp, "plan", w["id"])
        self.assertIsNotNone(out, "the plan stage must be claimable")
        self.assertFalse(out.get("breach"), str(out.get("breach")))
        approve = self.k.work.pending_approval(w["id"])
        self.assertIsNotNone(approve, "the plan reached the approval queue instead of being discarded")
        got = self.k.work.plan_for_approval(approve["id"])
        self.assertEqual(got["output"]["body"]["kind"], "plan", "the salvaged envelope is a plan")

    def test_a_written_result_still_wins(self):
        """Precedence: `result.json` is the body's own hand-in. The final-message
        extraction is the fallback, never the override."""
        emp, w = self.planner_on("File Planner", "writer", "final-message+ok")
        out = self.k.dispatch(emp, "plan", w["id"])
        self.assertIsNotNone(out)
        plan = self.k.work.latest(w["id"], "plan", "done")
        self.assertIsNotNone(plan, "the plan stage completed")
        log = self.log_of(plan["id"])
        self.assertNotIn("final message", log.lower(),
                         "a body that wrote result.json must not be recorded as salvaged")
        self.assertIsNotNone(self.k.work.pending_approval(w["id"]),
                             "a body that wrote result.json must still submit")

    def test_the_envelope_is_not_invented_when_the_body_printed_no_json(self):
        """The guard against the obvious cheat: a body that prints no JSON at all is
        still a failure. Extraction must never manufacture an envelope."""
        emp, w = self.planner_on("Silent Planner", "silent", "garbage")
        out = self.k.dispatch(emp, "plan", w["id"])
        self.assertIsNotNone(out)
        self.assertIn("no result.json and no JSON output", str(out.get("breach")),
                      "a body that prints no JSON is a contract breach")
        plan = self.k.work.latest(w["id"], "plan")
        self.assertEqual(plan["status"], "ready", "the breach returns the stage to the queue")
        log = self.log_of(plan["id"])
        self.assertNotIn("envelope from", log.lower(),
                         "a body that printed no JSON is not a submission")
        self.assertIsNone(self.k.work.pending_approval(w["id"]),
                          "a breach must not reach the approval queue")

    def test_the_run_log_names_where_the_envelope_came_from(self):
        """A salvaged run says so, so a reader can tell a harness that submitted from
        one the kernel recovered."""
        emp, w = self.planner_on("Salvaged Planner", "finalonly", "final-message+wrapped")
        out = self.k.dispatch(emp, "plan", w["id"])
        self.assertIsNotNone(out)
        plan = self.k.work.latest(w["id"], "plan", "done")
        self.assertIsNotNone(plan, "the salvaged plan was handed in")
        log = self.log_of(plan["id"])
        self.assertIn("[envelope from] final message", log,
                      "the log must record the salvage, so its source is never ambiguous")

    def test_the_final_message_salvage_applies_to_every_stdout_json_stage(self):
        """R1 is not plan-specific: an execute body that ends with its envelope is
        salvaged the same way, so the fix belongs in the driver's result reading rather
        than in one stage's handler."""
        self.install_driver("finalexec", "final-message+wrapped")
        maker = self.k.org.hire(name="Final Maker", title="Executor", dept="eng",
                                job_description="execute", roles=["executor"], driver="finalexec",
                                reason="R1", status="active")["id"]
        w = self.add(dept="eng")
        # plan and approve through the stock fake driver, then execute on the salvaged one
        out = self.k.dispatch(self.hod, "plan", w["id"])
        self.assertIsNotNone(out)
        a = self.k.work.pending_approval(w["id"])
        self.assertIsNotNone(a, "the stock plan reached the approval queue")
        self.k.approve(a["id"])
        out = self.k.dispatch(maker, "execute", w["id"])
        self.assertIsNotNone(out, "the execute stage must be claimable")
        self.assertFalse(out.get("breach"), str(out.get("breach")))
        ex = self.k.work.latest(w["id"], "execute", "done")
        self.assertIsNotNone(ex, "the execute result was handed in, not discarded")
        self.assertIn("hello.txt", json.dumps(self.k.work.output(ex["id"])["body"]),
                      "the salvaged execute body is what ran")
        self.assertTrue((self.workdir / "hello.txt").exists(),
                        "the salvaged executor actually did the work")
