"""Tests first for the venture importer's status vocabulary.

The importer decides whether a protocol is live from the protocol's own `status:`
field. That field is the one place the corpus is **unreliable**: the surveys of
2026-10-23 measured it contradicting the live registry in both directions, and the
kernel's corpus itself carries a banner on 55 of its 62 protocol files saying the
live registry is authoritative and the file's own `status:` is historical intent.

Two facts the kernel can defend without the registry:

1. **`superseded` is absent from the skip tuple.** Measured corpus-wide:
   **29 of 62 protocol files declare `status: superseded`**, and every one of them
   imports as though live. The importer's own comment states the rule it intends
   (a protocol that names a non-active status is honoured); `superseded` is the
   most-declared non-active status in the corpus and the one value the tuple omits.
2. **Quoting is not a factor.** An earlier survey claimed a double-quoted
   `status: "paused"` would fail to normalize. It does not: the front-matter reader
   strips both quote kinds, so `paused` reaches the tuple normalized whatever its
   quoting. That claim was disproven by a real import and is retracted, and the
   guard below pins the correct behaviour so it cannot be re-asserted.

Both guards are self-contained (they build their own scratch protocol), so they run
on any checkout; the second test adds the real corpus where it exists.
"""

import os
from pathlib import Path

from holon import cli
from tests.helpers import KernelCase

# The operator's B-side tree, never this repo. Located by environment, and the
# corpus comparison skips when it is absent, so a self-contained guard carries the
# rule everywhere (the same contract test_bridge_m4.py already documents).
CORPUS = Path(__file__).resolve().parents[2] / "ventures"
if not CORPUS.is_dir():
    CORPUS = Path(os.environ.get("HOLON_POOLS") or Path.home() / ".ventures" / "ventures")


class StatusVocabulary(KernelCase):
    """What the importer honours, and what it silently treats as live."""

    def _protocol(self, name: str, status: str, body: str = "Do the thing.") -> Path:
        """A one-protocol pool under a temp tree, so a test never touches the real corpus."""
        pool = self.tmp / "pool" / name
        (pool / "cron-jobs").mkdir(parents=True, exist_ok=True)
        (pool / "cron-jobs" / f"{name}.md").write_text(
            f"---\nname: {name}\nstatus: {status}\n---\n\n## Purpose\n{body}\n"
        )
        return pool

    def _skipped(self, out: dict) -> list[str]:
        return [s["title"] for s in out.get("skipped", [])]

    def test_a_superseded_protocol_is_honoured_not_treated_as_live(self):
        """The defect: `superseded` is the corpus's most-declared retired status and the
        importer's tuple omits it, so 29 of 62 files import as live. A protocol that
        names its own supersession is a declaration, and a declaration is honoured —
        the same rule the importer already applies to `paused` and `retired`."""
        pool = self._protocol("superseded-job", "superseded")
        out = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        self.assertIn("superseded-job", self._skipped(out),
                      "a protocol declaring itself superseded is a declaration, "
                      "not a protocol to import as live")

    def test_a_paused_protocol_is_skipped_whatever_its_quoting(self):
        """The retracted claim, pinned correctly: quoting is not a factor. The
        front-matter reader strips both quote kinds, so a bare, single- and
        double-quoted `paused` all reach the skip tuple normalized."""
        for quoted in ("paused", "'paused'", '"paused"'):
            pool = self._protocol("paused-job", quoted)
            out = cli.venture_import(pool, "t", dry_run=True, k=self.k)
            self.assertIn("paused-job", self._skipped(out),
                          f"status: {quoted} must reach the skip tuple normalized")

    def test_absence_is_not_a_declaration_of_pause(self):
        """The other half of the rule, and the reason the default stayed `active`: a
        protocol that says nothing is imported, because the operator is asking the
        kernel to import it. Measured on the real corpus — every protocol in the only
        importable pool declared no `status` at all, and a `!= "active"` default
        silently dropped every one."""
        pool = self._protocol("unstated-job", "")
        out = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        self.assertNotIn("unstated-job", self._skipped(out),
                         "absence is not a declaration of pause")

    def test_the_corpus_declares_superseded_and_none_of_it_is_honoured(self):
        """The corpus comparison: every `superseded`-declaring file the importer admits
        as live is a defect measured against the corpus itself, not against a stub.
        Where the corpus is absent this skips; the three guards above carry the rule."""
        files = sorted(p for p in CORPUS.glob("*/cron-jobs/*.md")
                       if "status: superseded" in p.read_text(errors="ignore"))
        if not files:
            self.skipTest(f"no superseded-declaring protocol under {CORPUS}")
        corpus_files = len(list(CORPUS.glob("*/cron-jobs/*.md")))
        self.assertLess(len(files), corpus_files,
                        "some protocols are live — the guard is about the superseded ones")
        pool = CORPUS / "quant-trading"
        if not (pool / "cron-jobs").is_dir():
            self.skipTest("no quant-trading pool to measure")
        out = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        admitted = {"monthly-retrain", "weekly-reconciliation"}
        self.assertEqual(admitted & set(self._skipped(out)), admitted,
                         "quant-trading's two superseded-declaring protocols, which the "
                         "live registry does not carry at all, must not be admitted as live")
        self.assertGreaterEqual(len(files), 20,
                                "the corpus-wide count this guard was written against was 29;"
                                " a pool tree with fewer superseded declarations is not the"
                                " corpus this guard was measured on")


class RefusalsAreNotShadowed(KernelCase):
    """A survey must report a refusal it cannot make you hear.

    `_would_refuse` predicts what the apply would decline, and it starts from the
    skips already collected — so a protocol the status tuple skips is never reached
    by the cadence check (`if r["title"] in named: continue`). That line is
    invisible until a protocol declares BOTH a retired status AND a cadence no
    unattended lane can justify. Then the survey reports "the protocol declares
    itself paused", the Chair sees an ordinary skip, and the refusal that was the
    whole reason for looking at that row is lost.

    Measured 2026-10-23 on the real corpus after `62d7a1d`: both cadence refusals
    in the corpus (`reddit-karma-lane-2h-draft-post` at 12 fires a day,
    `dad-arch-strategist-listener` at 26) declare `paused`/`superseded` as well, so
    both vanished behind the status skip. The guard below is built from a synthetic
    protocol carrying both facts, so it holds on any checkout.
    """

    def _protocol(self, name: str, status: str, schedule: str) -> Path:
        pool = self.tmp / "pool" / name
        (pool / "cron-jobs").mkdir(parents=True, exist_ok=True)
        (pool / "cron-jobs" / f"{name}.md").write_text(
            f"---\nname: {name}\nstatus: {status}\nschedule: '{schedule}'\n---\n\n## Purpose\nDo the thing.\n"
        )
        return pool

    def _reason(self, out: dict, title: str) -> str:
        return next((s["reason"] for s in out.get("skipped", []) if s["title"] == title), "")

    def test_a_retired_protocol_with_an_unjustifiable_cadence_names_both_reasons(self):
        """The regression: a protocol that declares itself retired AND fires too often
        must report the cadence too, not swallow it. The status skip is real and
        correct; the refusal is the part a reader cannot re-derive from the file."""
        pool = self._protocol("retired-and-frequent", "paused", "*/30 9-21 * * *")
        out = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        reason = self._reason(out, "retired-and-frequent")
        self.assertIn("declares itself paused", reason,
                      "the status skip is honoured")
        self.assertIn("26 times a day", reason,
                      "and the cadence refusal is named alongside it: a survey that reports"
                      " only the status skip hides the row that most needs the Chair")

    def test_a_refusal_is_reported_even_when_nothing_else_would_skip_the_row(self):
        """The control: without a retired status, the same cadence still refuses. This
        pins that the refusal machinery works, so a failure above is the shadowing
        and not a dead check."""
        pool = self._protocol("active-and-frequent", "", "*/30 9-21 * * *")
        out = cli.venture_import(pool, "t", dry_run=True, k=self.k)
        reason = self._reason(out, "active-and-frequent")
        self.assertIn("26 times a day", reason,
                      "an unretired protocol with the same cadence still refuses; if this"
                      " fails while the test above fails, the cadence guard itself is off")
