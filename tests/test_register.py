"""A9 (2026-10-22 audit): register drift is invisible until someone re-measures by hand.
TODO.md said H4 "blocked: a document knowledge base, waits on the Chair" while the K1–K4
knowledge-base layer had shipped and passed its own tests. A register whose rows lie corrupts
every plan built on it: work gets re-planned, real work gets skipped. Lint's `register` pack
flags a row that says "blocked" while the very mechanism it names demonstrably exists in the
distribution."""

import unittest
from pathlib import Path

import holon.integrity.lint as lint
from tests.helpers import KernelCase, REPO

REGISTER = REPO / "TODO.md"


class RegisterDrift(KernelCase):
    def test_a_blocked_register_row_for_a_shipped_mechanism_is_drift(self):
        """A9: a row whose status is `blocked` while the mechanism it names exists in the
        distribution is drift — H4's exact shape (blocked on a document KB while `holon kb`
        shipped). Lint names the row and the evidence."""
        self.assertIn("register", lint.PACKS, "the register pack must be registered in PACKS")
        findings = lint.run(self.k, ["register"])
        drift = [f for f in findings if "blocked" in f["message"] and "shipped" in f["message"]]
        self.assertFalse(drift, "the distribution's own register carries drifted rows: "
                        + "; ".join(f["message"] for f in drift))

    def test_the_pack_catches_a_blocked_row_whose_mechanism_exists(self):
        """The check itself, proved on a scratch register: a row saying `blocked` for a
        mechanism the distribution carries (the kb commands) is flagged; the same row saying
        `open` (its decision still pending) is not — blocked is the lie, open is the truth."""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="reg-"))
        (tmp / "TODO.md").write_text(
            "# TODO\n\n| id | status | item |\n|---|---|---|\n"
            "| X1 | blocked | a document knowledge base (`holon kb` commands, K1-K4) |\n"
            "| X2 | open | a document knowledge base (`holon kb` commands, K1-K4) |\n")
        bad = _drift_rows(tmp / "TODO.md")
        self.assertEqual([r[0] for r in bad], ["X1"], bad)
        self.assertIn("ships", bad[0][1])

    def test_a_blocked_row_for_a_mechanism_that_truly_wait_is_left_alone(self):
        """The pack must not cry wolf: a row blocked on something the distribution does not
        carry (an unimplemented command) is a real block and stays silent."""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="reg-"))
        (tmp / "TODO.md").write_text(
            "# TODO\n\n| id | status | item |\n|---|---|---|\n"
            "| Y1 | blocked | the federation pointer (no such command exists yet) |\n")
        self.assertEqual(_drift_rows(tmp / "TODO.md"), [])


def _drift_rows(register: Path):
    """Run the pack's own helper against a scratch register: the same function lint calls."""
    return lint.register_drift(register)


if __name__ == "__main__":
    unittest.main()
