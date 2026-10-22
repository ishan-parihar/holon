"""The artifacts placement law (2026-10-22 audit): every execute result's `artifacts` are real
paths, pinned to memory so the organization can find them, graded when they live outside the
reach, named by the brief, and never collected by gc while something points at them."""

import unittest

from holon.core import contract
from holon.core.contract import EnvelopeError
from holon.execution import grading
from tests.helpers import KernelCase


class ArtifactPathsGuard(unittest.TestCase):
    def test_artifacts_entries_must_be_paths(self):
        """A1: `artifacts` entries were free-form strings; a typo or a prose line passed the
        contract and rotted undetected. They must be absolute paths or workdir-relative
        fragments — no URLs, no double slashes, no empty."""
        for good in ("/tmp/out/report.md", "out/report.md", "report.md", "./x.txt"):
            contract.validate("execute", _env(artifacts=[good]))   # no raise
        for bad in ("https://example.com/x", "a //double", "", "/abs/ok.txt\nsecond"):
            with self.assertRaises(EnvelopeError) as cm:
                contract.validate("execute", _env(artifacts=[bad]))
            self.assertTrue(any("artifacts[0]" in p for p in cm.exception.problems), bad)

    def test_artifacts_may_be_omitted(self):
        self.assertEqual(contract.validate("execute", _env())["outcome"], "success")


def _env(**over):
    env = {"outcome": "success", "summary": "s", "documentation": "# d"}
    env.update(over)
    return env


class ArtifactPinning(KernelCase):
    def _executed(self, artifacts, outcome="success"):
        w = self.add("Build report", dept="eng", task_class="ops", risk="low")
        self.plan_and_approve(w, planner=self.hod, assignee=self.dev1,
                              writes=[str(self.workdir / "rpt.md")])
        c = self.k.claim("execute", self.dev1, task_id=self.k.work.latest(w["id"], "execute")["id"])
        out = self.k.submit(c.run["id"], self.exec_env(outcome=outcome, artifacts=artifacts))
        return w, c, out

    def test_submitted_artifacts_are_pinned_to_memory(self):
        """A2: nothing pinned an artifact anywhere, so the record rotted in `outputs` where only
        a verifier's glance ever saw it. Submit pins each to memory as kind=artifact in the
        work's scope with the run as provenance — recall and `memory show` answer 'where is X'."""
        w, c, out = self._executed([str(self.workdir / "rpt.md")])
        self.assertEqual(out["stage"], "execute")
        items = [i for i in self.k.memory.search("rpt.md", kinds=["artifact"], limit=10)]
        self.assertTrue(items, "the artifact was not pinned to memory")
        self.assertEqual(items[0]["scope"], "dept:eng",
                         "pinned to the work's scope, not the employee's")
        self.assertIn(c.run["id"], items[0]["provenance"],
                      "provenance names the run: memory show renders the edge back to it")

    def test_a_resubmitted_artifact_reinforces_rather_than_duplicates(self):
        """Two runs leaving the same artifact path (a re-run, a retry) must land on one item:
        memory's own fingerprint rule, proved at the pin seam."""
        self._executed([str(self.workdir / "rpt.md")])
        before = [i for i in self.k.memory.search("rpt.md", kinds=["artifact"], limit=10)]
        re = self.k.memory.add_item("dept:eng", "artifact", f"artifact: {self.workdir / 'rpt.md'}",
                                    "run:other")
        self.assertEqual(re["action"], "reinforced", "a second identical pin duplicated the item")
        after = [i for i in self.k.memory.search("rpt.md", kinds=["artifact"], limit=10)]
        self.assertEqual(len(after), len(before), "the re-pin created a twin item")

    def test_a_failed_result_pins_nothing(self):
        """A failure's artifacts are claims about work that did not finish: pinning them would
        teach the organization to trust paths that may not exist. Only successes and partials
        pin (a partial's artifacts are what it did produce)."""
        self._executed([str(self.workdir / "ghost.md")], outcome="failure")
        self.assertEqual([i for i in self.k.memory.search("ghost", kinds=["artifact"], limit=10)],
                         [], "a failed run pinned its phantom artifact")


class ArtifactGrading(unittest.TestCase):
    def test_an_artifact_outside_reach_raises_the_floor(self):
        """A6: grading walked `writes` and never looked at `artifacts` — a result claiming a
        durable artifact beyond the department's reach graded as G1 routine. An absolute
        artifact outside workdir+reach raises the floor with its reason; a relative one is
        workdir-relative, same as a relative write.

        The task carries no `task_class`, so the unclassified-task floor (G2) applies to
        every case here. The guard is what the artifact *adds*: the in-reach and relative
        cases add no artifact reason, and the outside one does."""
        task = {"id": "w1", "risk": "normal", "workdir": "/tmp/wd"}
        base = grading.grade_plan(None, task, {"kind": "plan"}, None, reach=["/tmp/wd"])
        in_reach = grading.grade_plan(None, task, {"kind": "plan", "artifacts": ["/tmp/wd/x.md"]},
                                      None, reach=["/tmp/wd"])
        self.assertEqual(in_reach["floor"], base["floor"], "an artifact inside the workdir is routine")
        self.assertFalse(any("artifact" in r for r in in_reach["reasons"]), in_reach["reasons"])
        out = grading.grade_plan(None, task, {"kind": "plan", "artifacts": ["/elsewhere/x.md"]},
                                 None, reach=["/tmp/wd"])
        self.assertEqual(out["floor"], 3,
                         "a department with a reach: an artifact beyond it is G3, exactly as a write is")
        self.assertTrue(any("artifact" in r for r in out["reasons"]), out["reasons"])
        rel = grading.grade_plan(None, task, {"kind": "plan", "artifacts": ["out/x.md"]},
                                 None, reach=["/tmp/wd"])
        self.assertEqual(rel["floor"], base["floor"], "a relative artifact is not graded")
        self.assertFalse(any("artifact" in r for r in rel["reasons"]), rel["reasons"])


class BriefTeachesPlacement(KernelCase):
    def test_the_execute_brief_names_the_placement_law(self):
        """A5: no law said where durable things live, so each agent improvised (a stray root
        directory grew on a live instance). The execute brief states it once, where the body
        reads it: workdir or a scope root, and every durable thing named in `artifacts`."""
        w = self.add("Build report", dept="eng", task_class="ops", risk="low")
        self.plan_and_approve(w, planner=self.hod, assignee=self.dev1,
                              writes=[str(self.workdir / "rpt.md")])
        c = self.k.claim("execute", self.dev1, task_id=self.k.work.latest(w["id"], "execute")["id"])
        text = (c.brief_dir / "BRIEF.md").read_text()
        self.assertIn("artifacts", text, "the brief names the field the envelope wants")
        self.assertIn("scope root", text,
                      "the placement law: the workdir or a declared scope root, not improvisation")


if __name__ == "__main__":
    unittest.main()
