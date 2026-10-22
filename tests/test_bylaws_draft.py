"""A10 (2026-10-22 audit): the pooling workflow. The units layer had the mechanism — one
manifest pools a capability, renders it into every pooler's brief, fences its binaries — and no
workflow from 'a script a department runs' to 'a declared unit'. `bylaws draft` proposes the
manifest from observed usage into a drafts area that changes no state; `bylaws promote` (chair)
moves it into the manifest directory after validation. The live instance held one manifest and
five ventures' worth of scattered scripts."""

import unittest

from tests.helpers import KernelCase


class DraftPromote(KernelCase):
    def test_a_draft_unit_lands_only_when_the_chair_promotes_it(self):
        """A10: an employee may propose a unit (a script its department keeps running ad hoc),
        but the proposal must change no state until the Chair promotes it — the same shape as
        the KB's blueprint draft/promote, one level down."""
        from holon.bylaws import units
        drafts = self.home.bylaws_units_dir.parent / "drafts"
        self.assertFalse(drafts.exists(), "the drafts area appears before any draft")
        out = units.draft(self.k, "notes-digest",
                          charter="turn the department's notes into a weekly digest",
                          service="digest", command="notes-digest run",
                          accepts="a notes directory", returns="markdown digest",
                          note="the only path that keeps digests in the brief")
        # a draft is a file in the drafts area, not a unit: sync state is untouched
        self.assertTrue((drafts / "notes-digest.toml").is_file(), out)
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM units").fetchone()[0], 0,
                         "a draft seeded the units tables")
        self.assertEqual(units.load(self.home)[0], {}, "a draft appeared as a unit")
        # and promote is the Chair's move
        with self.assertRaises(PermissionError):
            units.promote(self.k, "notes-digest", principal="emp:" + self.dev1)
        out2 = units.promote(self.k, "notes-digest", principal="chair")
        self.assertTrue((self.home.bylaws_units_dir / "notes-digest.toml").is_file(), out2)
        self.assertFalse((drafts / "notes-digest.toml").exists(), "promote left the draft behind")
        loaded, problems = units.load(self.home)
        self.assertIn("notes-digest", loaded, problems)
        self.assertEqual(problems, [], problems)

    def test_a_draft_refuses_an_invalid_slug_and_a_taken_name(self):
        """The drafts area must not become a second, unvalidated manifest directory: a bad
        slug is refused at draft, and a name that is already a unit is refused at promote."""
        from holon.bylaws import units
        with self.assertRaises(ValueError):
            units.draft(self.k, "Notes Digest!!", charter="x", service="s", command="c")
        units.draft(self.k, "taken", charter="c", service="s", command="c")
        (self.home.bylaws_units_dir).mkdir(parents=True, exist_ok=True)
        (self.home.bylaws_units_dir / "taken.toml").write_text(
            'charter = "c"\ncategory = "service"\ntier = "foundation"\n'
            'roots = []\npool_of = []\nconsumed = []\n')
        with self.assertRaises(ValueError):
            units.promote(self.k, "taken", principal="chair")

    def test_a_promoted_unit_must_pass_validation_before_it_lands(self):
        """Promote runs the same validation lint does (bind.pool: pooled departments must
        exist), so a draft cannot land as a unit that would fail its first lint."""
        from holon.bylaws import units
        units.draft(self.k, "pooler", charter="c", service="s", command="c",
                    pool_of=["no-such-dept"])
        with self.assertRaises(ValueError):
            units.promote(self.k, "pooler", principal="chair")


if __name__ == "__main__":
    unittest.main()
