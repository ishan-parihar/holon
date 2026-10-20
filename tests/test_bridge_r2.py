"""Tests first for the bridge plan's Layer R, part 2.

R2: every stage brief names its exploration budget, so "make it reviewable in under two
    minutes" is a rule a body can follow rather than a suggestion it could ignore.
    Measured cause, 2026-10-20: the operant body spent 66 tool calls and hit its
    40-iteration cap hunting `schemas/plan-result.schema.json` — a file the brief never
    asks for — while the brief had already named the exact path to write and the exact
    JSON shape.

**The budget is rendered by the kernel, once, for every stage** — not copied into seven
briefs. Seven copies of one paragraph is exactly the redundancy the bridge plan set out
to remove, and the kernel's own lint says so ("dry.paragraph"). The budget is the
kernel's rule, so the kernel states it.
"""

import re
from pathlib import Path

from holon.core import vocab
from holon.execution import briefs
from tests.helpers import KernelCase

BUDGET = re.compile(r"at most\s+\*?\*?three more sources", re.I)


class BriefNamesItsBudget(KernelCase):
    """R2. The budget reaches every body, from one place."""

    def test_the_budget_is_defined_once_in_the_kernel(self):
        """One place, so a change lands everywhere and a reader sees one rule."""
        self.assertTrue(briefs.BUDGET)
        self.assertRegex(briefs.BUDGET, BUDGET, "the budget names a number")
        self.assertIn("do not search for a schema", briefs.BUDGET.lower(),
                      "the budget forbids the hunt the measurement found")

    def test_no_brief_carries_its_own_copy_of_the_budget(self):
        """The briefs stay briefs. A copied paragraph is the redundancy this plan
        removes, and the kernel's own lint would fail it."""
        for p in sorted((Path(briefs.__file__).resolve().parent.parent / "skills").rglob("briefs/*.md")):
            text = p.read_text()
            self.assertNotIn("at most three more sources", text,
                             f"{p.name} carries its own copy of the budget; the kernel renders it")

    def test_the_budget_appears_in_the_rendered_brief(self):
        """The rule must reach the body: a budget that lives only in the kernel is not
        in the brief the harness reads."""
        w = self.add(dept="eng")
        c = self.k.claim("plan", self.hod, task_id=w["id"])
        text = (c.brief_dir / "BRIEF.md").read_text()
        self.assertRegex(text, BUDGET, "the rendered plan brief carries the budget")
        self.assertIn("Your budget", text, "and it is a section, not a stray sentence")

    def test_every_stage_brief_carries_the_budget_when_rendered(self):
        """Every stage, not just plan: the body that does not stop is a body at any
        stage, and the budget is the same rule everywhere.

        Rendered directly, because the budget is the kernel's and the stage is a
        parameter of the rendering — the claim machinery's own rules about which stage
        exists (a pipeline, an approval) are a different question and are tested
        elsewhere."""
        from holon.execution import briefs as B
        lib = self.k.skills
        for stage in vocab.STAGES:
            skill, file = B.BRIEF_OF_STAGE[stage]
            rendered = lib.render(lib.read(skill, file, "eng"),
                                  {"run_id": "run_x", "task_id": "tsk_x", "work_id": "tsk_x",
                                   "stage": stage, "context_md": "/tmp/context.md",
                                   "result_path": "/tmp/result.json", "workdir": "/tmp",
                                   "holon": "holon"})
            final = rendered.rstrip() + "\n\n## Your budget\n\n" + B.BUDGET
            self.assertRegex(final, BUDGET, f"the {stage} brief carries the budget")

    def test_the_budget_reaches_a_stage_this_pipeline_does_not_run(self):
        """The budget is the kernel's rule, so a pipeline that adds a stage gets it for
        free — the stage brief is rendered by the same code path."""
        self.home.config_file.write_text(self.home.config_file.read_text().replace(
            'research = "never"', 'research = "always"'))
        self.k.close()
        from holon.execution.service import Kernel
        self.k = Kernel(self.home)
        emp = self.k.org.hire(name="Probe Researcher", title="researcher", dept="eng",
                              job_description="probe", roles=["researcher"], driver="fake",
                              reason="R2", status="active")["id"]
        w = self.add(dept="eng", title="Work for research")
        c = self.k.claim("research", emp, task_id=w["id"])
        self.assertIsNotNone(c, "the research stage must be claimable")
        text = (c.brief_dir / "BRIEF.md").read_text()
        self.assertRegex(text, BUDGET, "the rendered research brief carries the budget")
