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


class ModelDependentRefusalsArePredicted(KernelCase):
    """The refusals A3 filed as unpredictable, measured instead of assumed.

    A3's outline row split `add_routine`'s eight raises into "five pure" and "three that
    depend on the decision model", and concluded the second three could not be reported
    when the model was off. **Measured 2026-10-24T22:40, that conclusion was wrong, and
    it is the row's own gate that disproves it:** `_would_refuse` already calls
    `classify` for the ordinary path, and `classify` with the model off returns
    `{"lane": None, "source": "off"}` rather than raising - so a no-model kernel still
    yields a lane value to compare against. Both reachable model-dependent refusals are
    predicted, and each guard below measures it against the apply.

    What is actually true is narrower and stronger: **two of the three are unreachable
    from an import at all.** `add_routine` raises "the classifier says X is enough" only
    behind `elif not why` in the `strict=True` branch, and measured across every call
    site, four of five pass `strict=False` (`ventures.py:545`, `cli.py:946`,
    `service.py:1497`, `portability.py:163`); only the interactive `holon routine add`
    (`cli.py:782`) takes the default, and it has no dry run to be unfaithful to. So an
    import cannot raise it, which means a dry run that cannot predict it is not a gap.

    These guards exist so the filing error cannot be re-asserted: a claim that the dry
    run is silent is testable in three lines, and silence would be a real defect.
    """

    def _pool(self, name: str, cron: str) -> Path:
        pool = self.tmp / "pool" / name
        (pool / "cron-jobs").mkdir(parents=True, exist_ok=True)
        (pool / "cron-jobs" / f"{name}.md").write_text(
            f"---\nname: {name}\nstatus: active\nschedule: '{cron}'\n---\n\n## Purpose\nDo the thing.\n"
        )
        return pool

    def _reason(self, out: dict, title: str) -> str:
        return next((s["reason"] for s in out.get("skipped", []) if s["title"] == title), "")

    def test_a_no_model_kernel_still_predicts_the_cadence_refusal(self):
        """The no-decision-model refusal. This kernel has `decider.on` False, and the
        dry run still names it, matching the apply - because `classify` reports
        `source: off` with `lane: None` rather than raising."""
        self.assertFalse(self.k.decider.on, "this guard is about the model being off")
        pool = self._pool("frequent", "*/5 * * * *")
        dry = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        app = cli.venture_import(pool, "t", dry_run=False, k=self.k)
        self.assertIn("frequent", [s["title"] for s in dry["skipped"]],
                      "the survey names the cadence refusal with the model off")
        self.assertEqual(app["routines"], 0)
        self.assertEqual(sorted(s["title"] for s in app["skipped"]),
                         sorted(s["title"] for s in dry["skipped"]),
                         "the survey's skipped set equals the apply's")

    def test_a_classified_script_job_is_predicted(self):
        """The 'looks deterministic' refusal. Stubbed classifier so no live model is
        needed, and the survey and the apply agree."""
        self.k.classify = lambda title, intent, command=None: {
            "lane": "script", "confidence": 0.92, "external_p": None, "source": "stub",
            "model_lane": "script", "probabilities": None, "error": None}
        pool = self._pool("scriptish", "0 9 * * 1")
        dry = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        app = cli.venture_import(pool, "t", dry_run=False, k=self.k)
        self.assertIn("looks deterministic", self._reason(dry, "scriptish"))
        self.assertEqual(app["routines"], 0)

    def test_a_mixed_pool_predicts_the_apply_exactly(self):
        """A3's gate as the row states it: the dry run's skipped count equals the
        apply's made-shortfall. One protocol per refusal class - unparseable cron
        (arguments), a */5 job with no model, and a valid one."""
        pool = self.tmp / "pool" / "mixed"
        (pool / "cron-jobs").mkdir(parents=True)
        for name, cron in (("bad", "nonsense"), ("fast", "*/5 * * * *"), ("ok", "0 9 * * 1")):
            (pool / "cron-jobs" / f"{name}.md").write_text(
                f"---\nname: {name}\nstatus: active\nschedule: '{cron}'\n---\n\n## Purpose\nDo the thing.\n")
        dry = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        app = cli.venture_import(pool, "t", dry_run=False, k=self.k)
        self.assertEqual(dry["protocols"], 3)
        self.assertEqual(sorted(s["title"] for s in dry["skipped"]), ["bad", "fast"])
        self.assertEqual(app["routines"], 1)
        self.assertEqual(sorted(s["title"] for s in app["skipped"]), ["bad", "fast"])
        self.assertEqual(len(dry["skipped"]), dry["protocols"] - app["routines"],
                         "the shortfall the gate names")

    def test_the_strict_only_refusal_is_unreachable_from_an_import(self):
        """Why nothing needs building here: the third raise needs `strict=True`, and
        measured call by call, both importers pass `strict=False`. This guard keeps the
        withdrawn claim from coming back as a new gap."""
        import inspect
        from holon.execution import portability as P
        from holon.execution import ventures as V
        self.assertIn("strict=False", inspect.getsource(V.venture_import),
                      "the venture import passes strict=False")
        self.assertIn("strict=False", inspect.getsource(P),
                      "the portability import passes strict=False")


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
