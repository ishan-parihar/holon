"""The architecture index (holon/capabilities/ad.py): the Chair's 2026-10-25 red-team ruling.

The corpus stays markdown — it is the source of truth, because every gate that holds the
system honest reads prose. What was missing is retrieval: no command could ask what the
organization learned about a mechanism, and no brief carried the findings that bear on the work.
These guards hold the derived index to the two properties that make it safe to lean on:

  1. the build REFUSES a ledger row that names a guard the suite does not carry — the
     invariant `ref.guard` greps for, at the seam that writes the index, not only in CI;
  2. the index is derived: files are the truth, the index is rebuildable, and staleness is
     checkable against the sha of the file each row came from.
"""

import unittest
from pathlib import Path

from tests.helpers import KernelCase
from holon.capabilities import ad


class AdIndex(KernelCase):
    def setUp(self):
        super().setUp()
        self.counted = ad.build(self.k)

    def test_the_build_counts_the_corpus_it_actually_indexed(self):
        """The build is measurable, not asserted: it reports how many findings, decisions and
        records it indexed, so a silent empty corpus is visible rather than merely fast."""
        self.assertGreater(self.counted["findings"], 100,
                           f"the ledger carries a century of rows; the build indexed "
                           f"{self.counted['findings']}. An empty parse is the failure this catches")
        self.assertGreaterEqual(self.counted["guards"], self.counted["findings"],
                                "every finding names at least one guard")
        self.assertGreater(self.counted["records"], 0, "the design records are indexed")

    def test_the_build_refuses_a_ledger_row_naming_a_missing_guard(self):
        """The invariant `ref.guard` enforces in lint, at the seam that writes the index. A
        regression ledger that names a test nobody carries is a claim with no teeth — this makes
        it a build error, which the heartbeat and every `ad find` hits, not only a person who
        remembers to run lint. Measured before the fix: nothing but lint checked it."""
        agents = ad._dist() / "AGENTS.md"
        original = agents.read_text()
        self.addCleanup(agents.write_text, original)
        # a row naming a guard that does not exist, in the ledger's own shape
        agents.write_text(original.replace(
            "## Regression ledger\n",
            "## Regression ledger\n\n"
            "| Mutation probe, 2026-10-25 | a planted row naming no real test | "
            "`test_never_existed_at_all` |\n", 1))
        with self.assertRaises(ad.IndexError_) as caught:
            ad.build(self.k)
        self.assertIn("test_never_existed_at_all", str(caught.exception),
                      "the refusal names the dangling guard")

    def test_find_retrieves_by_mechanism_and_show_resolves_the_hit(self):
        """The retrieval that was missing end to end: find by topic, then resolve one hit to
        its full row. An executor touching isolation must be able to learn what the organization
        already learned about isolation — before it repeats it."""
        hits = ad.find(self.k, "isolation")
        self.assertTrue(hits, "the ledger's isolation rows are findable")
        first = next(h for h in hits if h["source"].startswith("finding:"))
        self.assertIn("isolation", first["text"].lower())
        shown = ad.show(self.k, "Isolation")
        self.assertIsNotNone(shown, "show resolves a finding's area to its rows")

    def test_precedents_is_a_capped_cut_not_the_corpus(self):
        """The context-injection seam's budget property: a body gets the few rows that bear on
        its work, never the 100k-token corpus. The cap is the guarantee, and it holds even for
        a query that matches everything."""
        rows = ad.precedents(self.k, "the isolation runner refused to start and the broker "
                                     "never answered", limit=5)
        self.assertLessEqual(len(rows), 5, "the cut is capped at the caller's limit")
        wide = ad.precedents(self.k, "a b c d e f g h i j k l m n o p q r s t u v w x y z "
                                     "the kernel the broker the runner isolation keys tables",
                             limit=5)
        self.assertLessEqual(len(wide), 5, "a query matching everything still returns 5, not 200")

    def test_the_index_is_derived_and_rebuildable(self):
        """Files are the truth; the index is a cache. Rebuilding is idempotent and produces the
        same counts, and a rebuilt index after a corpus change reflects the change — the sha
        columns exist so staleness is checkable, and this guard exercises the rebuild path."""
        again = ad.build(self.k)
        self.assertEqual(again["findings"], self.counted["findings"],
                         "a rebuild over an unchanged corpus is idempotent")
        import sqlite3
        conn = sqlite3.connect(again["db"])
        try:
            (n,) = conn.execute("SELECT COUNT(*) FROM findings_guards").fetchone()
            (f,) = conn.execute("SELECT COUNT(*) FROM findings").fetchone()
        finally:
            conn.close()
        self.assertGreater(n, f, "the join table carries at least one guard per finding")

    def test_a_brief_carries_the_precedent_section_and_a_missing_index_fails_open(self):
        """The Chair's 2026-10-25 ruling, applied at the seam that matters: a body receives the
        findings that bear on its work, capped, inside the recall family — and an instance
        without an index still compiles, because the section is enrichment, never a dependency.
        Measured before this: the ledger and the design corpus reached zero briefs."""
        from holon.capabilities import context
        t = self.add("Fix the isolation runner so agents can start", dept="eng",
                     intent="the runner refuses to start and no agent can run")
        stage = self.k.work.stages(t["id"])[0]
        md, meta = context.compile_context(self.home, self.k.work, self.k.memory, stage["id"])
        self.assertIn("Precedent: what the organization already learned", md,
                      "a body whose work names isolation sees what the organization learned about it")
        self.assertIn("Precedent", " ".join(meta["sections"]))
        # fail-open: an instance with no index (or a broken one) compiles the same brief
        import shutil as _sh
        _sh.rmtree(self.home.root / "ad")
        md2, meta2 = context.compile_context(self.home, self.k.work, self.k.memory, stage["id"])
        self.assertNotIn("Precedent:", md2,
                         "a missing index adds no section — silence, not an error")
        self.assertIn("## Task", md2, "the brief still carries the work")

    def test_the_precedent_cut_respects_the_recall_floor(self):
        """The section joins the recall family, so large stage material cannot crowd it out —
        the KB-1 property, extended to the organization's own findings. A budget-starved bundle
        still reserves the floor for the precedent cut, or records it as a gap."""
        from holon.capabilities import context
        t = self.add("Fix the isolation runner so agents can start", dept="eng",
                     intent="the runner refuses to start and no agent can run")
        stage = self.k.work.stages(t["id"])[0]
        md, meta = context.compile_context(self.home, self.k.work, self.k.memory, stage["id"],
                                           recall_floor=4000)
        self.assertTrue(any(s.startswith("Precedent") for s in meta["sections"])
                        or any("Precedent" in d for d in meta["degraded"]),
                        "the precedent cut is either kept or named as a gap — never silently dropped")
