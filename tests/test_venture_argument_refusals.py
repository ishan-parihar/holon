"""Tests for the dry run that cannot see the argument refusals it is about to make.

The defect, A3. `add_routine` raises eight times. A dry run of
`venture_import` never called it, and `_would_refuse` re-derived only the
refusals that come from the decision model's `cls`. So the five refusals that are pure
argument validation — a trigger named twice, an event that is not spelled
`event:<pattern>`, a cron that does not parse, a lane and a command that disagree — were
invisible to the survey.

A pool whose protocols all fail one of those five reported `protocols: 2, skipped: []`,
and the apply of the same pool made 0. That is the record this row was filed against,
and it is the same shape as the paused-status bug: an import that cannot tell a clean
import from one that lost everything is not a foundation.

Measured 2026-10-24: of the 8 raises, 5 are functions of the arguments alone and 3 depend
on `cls`, which comes from the decision model. Only the first five can be extracted, and
extracting them is the whole of this fix. The three that remain are named as a boundary in
`_validate_routine_args`'s own docstring, because a reader deserves to know what the dry
run still cannot see.
"""

from holon import cli
from holon.execution.ventures import _would_refuse
from tests.helpers import KernelCase


class ArgumentRefusalsAreReported(KernelCase):
    """A survey names the refusals the apply would raise on its own arguments."""

    def _row(self, name: str, *, cron: str = "0 9 * * 1", command=None, lane=None,
             event=None) -> dict:
        return {"title": name, "cron": cron, "intent": "Do the thing.", "command": command,
                "lane": lane, "event": event, "paused": False, "foreign_runtime": False,
                "provenance": {"fields": {}}}

    def _protocol_with_cron(self, name: str, cron: str) -> tuple:
        pool = self.tmp / "pool" / name
        (pool / "cron-jobs").mkdir(parents=True, exist_ok=True)
        (pool / "cron-jobs" / f"{name}.md").write_text(
            f"---\nname: {name}\nstatus: active\nschedule: '{cron}'\n---\n\n## Purpose\nDo the thing.\n"
        )
        return pool, [self._row(name, cron=cron)]

    def test_a_protocol_with_no_schedule_is_not_refused_by_the_argument_check(self):
        """The regression this fix caused and then closed. A protocol with no `schedule:`
        gets an empty `cron` from `legacy_cron`, and refusing it as "no trigger named"
        would drop every protocol in a pool whose schedules are pending. Measured on the
        full suite: the first version refused `unstated-job` and its own guard failed,
        which is how the boundary was found."""
        pool = self.tmp / "pool" / "no-schedule"
        (pool / "cron-jobs").mkdir(parents=True)
        (pool / "cron-jobs" / "unstated-job.md").write_text(
            "---\nname: unstated-job\nstatus: \n---\n\n## Purpose\nDo the thing.\n"
        )
        out = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        self.assertNotIn("unstated-job", [s["title"] for s in out.get("skipped", [])],
                         "a protocol declaring no schedule is not refused as an argument defect")

    def test_a_cron_that_does_not_parse_is_named_by_the_survey(self):
        """The pure-validation refusal: `Cron(cron)` raises, so the apply refuses it.
        The dry run must say so, and it must not consult the decision model to do it,
        because the decision model can be off, rate-limited, or absent."""
        pool, rows = self._protocol_with_cron("unparseable", "not a cron")
        out = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        refusals = out.get("skipped", [])
        self.assertIn("unparseable", [s["title"] for s in refusals],
                      "a protocol whose cron does not parse is refused, and named as such")

    def test_a_trigger_named_both_ways_is_named_by_the_survey(self):
        """`bool(cron) == bool(event)`: a row carrying a cron and an event together is
        refused by the apply on its arguments alone, so the survey must report it."""
        r = dict(self._row("both-triggers", cron="0 9 * * 1"), event="event:ping")
        out = _would_refuse(self.k, [r], [])
        reasons = [s["reason"] for s in out]
        self.assertTrue(any("exactly one" in v for v in reasons),
                        f"a row with a cron and an event is refused as a trigger conflict, got {reasons}")

    def test_an_event_that_is_not_spelled_event_pattern_is_named(self):
        """`event and not event.startswith('event:')` is an argument check, so it is
        predictable without the model."""
        r = dict(self._row("bare-event", cron=None), event="ping")
        out = _would_refuse(self.k, [r], [])
        reasons = [s["reason"] for s in out]
        self.assertTrue(any("event:<pattern>" in v for v in reasons),
                        f"a malformed event is refused, got {reasons}")

    def test_the_classifier_is_not_consulted_for_an_argument_refusal(self):
        """The point of the extraction: the argument check runs before any `cls` call,
        so a refusal is reported with the decision model off. This is the property the
        old code lacked, and it is what makes the dry run trustworthy."""
        r = dict(self._row("unparseable", cron="not a cron"))
        calls = {"n": 0}
        real = self.k.classify

        def counting(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw)

        self.k.classify = counting
        try:
            out = _would_refuse(self.k, [r], [])
        finally:
            self.k.classify = real
        self.assertEqual(calls["n"], 0,
                         "an argument refusal is decided without asking the decision model")
        self.assertIn("unparseable", [s["title"] for s in out])

    def test_a_row_that_passes_arguments_still_reaches_the_classifier(self):
        """The extraction must not swallow the model-dependent refusals. A clean
        argument set falls through to `classify`, which was the pre-existing behaviour
        and stays intact — this is the guard against adding a check that hides the ones
        the survey already had."""
        r = self._row("clean-args", cron="0 9 * * 1")
        calls = {"n": 0}
        real = self.k.classify

        def counting(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw)

        self.k.classify = counting
        try:
            _would_refuse(self.k, [r], [])
        finally:
            self.k.classify = real
        self.assertGreaterEqual(calls["n"], 1,
                                "a row with valid arguments still reaches the classifier")

    def test_the_survey_reports_the_argument_refusal_beside_a_status_skip(self):
        """Both are true of the same row: it declares itself paused AND its cron does
        not parse. The status skip is the kernel's decision and stays first; the refusal
        is appended to that row's reason rather than replacing it, which is the same rule
        the cadence refusal already follows."""
        pool, rows = self._protocol_with_cron("paused-and-unparseable", "not a cron")
        # paused=True is what the status skip keys on
        rows[0]["paused"] = True
        out = _would_refuse(self.k, rows, [{"title": "paused-and-unparseable",
                                           "reason": "the protocol declares itself paused"}])
        reason = next(s["reason"] for s in out if s["title"] == "paused-and-unparseable")
        self.assertIn("declares itself paused", reason, "the status skip is honoured")
        self.assertIn("AND".replace("AND", "ALSO"), reason,
                      f"the argument refusal is appended to the same row, got {reason!r}")


class TheBoundaryIsWhatIsLeft(KernelCase):
    """The three refusals that stay behind, named so the survey's silence has a shape."""

    def test_the_helper_raises_for_each_of_the_five_argument_cases(self):
        """Each of the five checks in the extracted helper raises, so the dry run can
        catch all five. A check that silently returned instead of raising would be a
        refusal the survey loses."""
        from holon.execution.service import Kernel
        v = Kernel._validate_routine_args
        cases = [
            dict(cron=None, event=None),                                  # neither trigger
            dict(cron="0 9 * * 1", event="event:ping"),                   # both triggers
            dict(cron=None, event="ping"),                                # malformed event
            dict(cron="nonsense"),                                        # unparseable cron
            dict(lane="not-a-lane"),                                      # bad lane
            dict(lane="script", command=None),                            # script without a command
            dict(command="echo hi", lane="full"),                         # command with another lane
        ]
        for kw in cases:
            with self.assertRaises(ValueError, msg=f"{kw} should raise"):
                v("t", "intent", **kw)

    def test_the_valid_arguments_raise_nothing(self):
        """The other half of the same contract: a valid set passes, so the helper does
        not refuse a routine the apply would accept."""
        from holon.execution.service import Kernel
        self.assertIsNone(Kernel._validate_routine_args(
            "t", "intent", cron="0 9 * * 1", command=None, lane=None, event=None))
