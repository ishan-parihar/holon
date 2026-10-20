"""Tests first for the bridge plan's Layer R, part 2.

R2: every stage brief names its exploration budget, so "make it reviewable in under two
    minutes" is a rule a body can follow rather than a suggestion it could ignore.
    Measured cause, 2026-10-20: the operant body spent 66 tool calls and hit its
    40-iteration cap hunting `schemas/plan-result.schema.json` — a file the brief never
    asks for — while the brief had already named the exact path to write and the exact
    JSON shape.
"""

import re
from pathlib import Path

from holon.execution import briefs
from tests.helpers import KernelCase

BRIEFS = sorted((Path(briefs.__file__).resolve().parent.parent / "skills").rglob("briefs/*.md"))
# the phrase wraps across lines in the rendered brief, so match the whole budget sentence
BUDGET = re.compile(r"consult at most\s+three more sources", re.I)


class BriefNamesItsBudget(KernelCase):
    """R2. The budget is in the brief, and it names a number."""

    def test_every_stage_brief_names_a_numbered_exploration_budget(self):
        """A budget a body can follow carries a number. One without a number is prose."""
        self.assertEqual(len(BRIEFS) >= 5, True, f"expected the stage briefs, got {BRIEFS}")
        missing = [str(p.relative_to(p.parents[3])) for p in BRIEFS if not BUDGET.search(p.read_text())]
        self.assertEqual(missing, [], f"these briefs name no exploration budget: {missing}")

    def test_the_budget_appears_in_the_rendered_brief_not_only_the_source(self):
        """The rule must reach the body: a brief that is only budgeted in the skill file
        does nothing. The rendered brief is what the harness reads."""
        self.home.drivers_dir.mkdir(parents=True, exist_ok=True)
        w = self.add(dept="eng")
        c = self.k.claim("plan", self.hod, task_id=w["id"])
        text = (c.brief_dir / "BRIEF.md").read_text()
        self.assertRegex(text, BUDGET, "the rendered plan brief carries the budget")

    def test_the_budget_forbids_hunting_for_a_schema_the_brief_already_carries(self):
        """The specific waste the measurement found: a body searching the filesystem for
        a result schema while the brief already contains the exact shape."""
        text = next(p.read_text() for p in BRIEFS if p.name == "plan.md")
        self.assertIn("do not search for a schema", text.lower().replace("result shape", "schema"),
                      "the brief must say the shape is already here")
        self.assertRegex(text, r"(do not|never) .*(search|hunt|look)", "the brief forbids the hunt")

    def test_the_budget_is_one_rule_per_stage_not_repeated_prose(self):
        """The budget is a single numbered rule, not a paragraph repeated in each brief:
        one place to change it, and a body that reads the rules reads it once."""
        for p in BRIEFS:
            hits = BUDGET.findall(p.read_text())
            self.assertEqual(len(hits), 1,
                             f"{p.name} states its budget {len(hits)} times; one rule per stage")
