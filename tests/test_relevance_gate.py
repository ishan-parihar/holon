import json
import unittest
from pathlib import Path

from tests.helpers import KernelCase

POOL_ORG = """pool:
  name: legacy-organism
  purpose: a legacy trading system
"""

FOREIGN = "---\nname: daily-live-monitor\nschedule: '0 7 * * *'\nruntime: vps-systemd\ndescription: watch the legacy streams\n---\n1. Pull VPS logcheck summary.\n"
DOMESTIC = "---\nname: disk-watch\nschedule: '0 7 * * *'\ndescription: record disk usage\n---\n1. df -h .\n"


class RelevanceGateTest(KernelCase):
    """A proposal comes from relevance to this organization, not from the bare
    existence of a legacy system's protocol files.

    Measured 2026-10-23. An import proposed two routines that declared
    `runtime: vps-systemd` and `runtime: manual` — the trading system's VPS
    monitor and its weekly reconciliation, whose data and whose clock live on
    a box this kernel does not serve. The Chair cancelled all four pending
    routines and ruled: proposals come from relevance, not from historical
    correlation of legacy agentic-os systems.

    The rule is the protocol's own declaration, judged by the importer — not
    the workdir. The workdir was the WRONG discriminator and is recorded here
    as the mistake it was: an imported department's scope roots ARE the pool's
    own directories (`ventures.venture_import`), so a pool outside the
    instance is the normal, correct case. Fencing the location forbids the
    kernel's own import law; fencing the declaration fences what the law
    intends.
    """

    def _pool(self, body=FOREIGN, name="daily-live-monitor"):
        p = self.tmp / "pool"
        (p / "cron-jobs").mkdir(parents=True, exist_ok=True)
        (p / "scripts").mkdir(parents=True, exist_ok=True)
        (p / "_org.yaml").write_text(POOL_ORG)
        (p / "cron-jobs" / "monitor.md").write_text(body)
        return p

    def test_a_protocol_declaring_a_foreign_runtime_is_refused(self):
        from holon.cli import legacy_cron
        r = legacy_cron(self._pool() / "cron-jobs" / "monitor.md")
        self.assertTrue(r["foreign_runtime"], r)
        self.assertIn("runtime", r["provenance"]["fields"], "the declaration is preserved, never lost")

    def test_a_protocol_declaring_no_runtime_is_not_fenced(self):
        from holon.cli import legacy_cron
        r = legacy_cron(self._pool(DOMESTIC) / "cron-jobs" / "monitor.md")
        self.assertFalse(r["foreign_runtime"], r)

    def test_the_kernels_own_runtimes_are_not_foreign(self):
        from holon.cli import _FOREIGN_RUNTIME_RE
        for served in ("holon", "kernel", "agent", "driver", "local", "this-instance", ""):
            self.assertIsNone(_FOREIGN_RUNTIME_RE.search(served), served)

    def test_the_import_command_names_the_refusal_in_the_survey(self):
        from holon.cli import cmd_routine_import
        f = self._pool() / "cron-jobs" / "monitor.md"
        args = type("A", (), {"files": [str(f)], "dept": "eng", "apply": False, "json": True})()
        args.principal = type("P", (), {"kind": "chair", "actor": "chair"})()
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_routine_import(args, self.k)
        rows = json.loads(buf.getvalue())
        err = (rows[0].get("error") or "").lower()
        self.assertIn("relevance", err, rows)

    def test_the_venture_dry_run_names_it(self):
        """A survey that prints a clean line for a file the apply drops is the
        silence this command had already carried twice."""
        from holon.execution.ventures import venture_import
        row = venture_import(self.k, self._pool(), "observability", dry_run=True)
        reason = next((s["reason"] for s in row["skipped"] if s["title"] == "daily-live-monitor"), None)
        self.assertIsNotNone(reason, row)
        self.assertIn("relevance", reason.lower(), reason)

    def test_the_venture_apply_skips_it_and_names_why(self):
        from holon.execution.ventures import venture_import
        # the import CREATES the department it is given (and refuses one that already
        # exists), so this test must not pre-create it.
        row = venture_import(self.k, self._pool(), "legacy-ops", dry_run=False)
        reason = next((s["reason"] for s in row["skipped"] if s["title"] == "daily-live-monitor"), None)
        self.assertIsNotNone(reason, row)
        self.assertEqual(0, row["routines"], row)

    def test_a_pool_outside_the_instance_is_still_a_valid_import(self):
        """The workdir is NOT the gate: an imported department's scope roots are
        the pool's own directories, so a pool outside the instance imports
        cleanly — the case my first attempt at this rule got wrong."""
        from holon.execution.ventures import venture_import
        # the import creates its own department, and a pool outside the instance is
        # the normal case (the department's scope roots ARE the pool's directories)
        row = venture_import(self.k, self._pool(DOMESTIC, "disk-watch"), "pool-reach", dry_run=False)
        self.assertTrue(Path(row["pool"]).is_absolute())
        self.assertFalse(Path(row["pool"]).is_relative_to(self.home.root), "the fixture pool is outside")
        self.assertEqual(1, row["routines"], row)

    def test_a_routine_written_by_hand_is_not_fenced(self):
        """The Chair's own `routine add` has no protocol to declare a foreign
        runtime: the gate judges an import's declaration, not a person's command."""
        r = self.k.add_routine("disk-watch", "record disk usage", dept="eng",
                               cron="0 7 * * 1", imported=False)
        self.assertNotIn("relevance", json.dumps(r))


if __name__ == "__main__":
    from pathlib import Path  # noqa: F401
    unittest.main()
