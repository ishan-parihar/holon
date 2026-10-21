"""Tests first for the bridge plan's M4 — the venture department builder.

A venture is four artifacts — `_org.yaml`, `cron-jobs/*.md`, `smoke.yaml`, `scripts/`
— and each maps to a different kernel feature. Nothing read all four, so a venture
department had to be built by hand.

`holon venture import <path> --dept <slug>` reads all four at once:
- `_org.yaml` → the department's charter and its scope roots;
- `cron-jobs/*.md` → routines, through the repaired importer (nothing silently dropped);
- `smoke.yaml` → the department's checks (YAML in, the kernel's TOML out);
- `scripts/` → the script lane's commands.

Dry-run by default. `--apply` creates the department and its routines, leaves the
routines **unapproved**, and computes each routine's outside-reach check on the
*fidelity-repaired* text — not the seven fields the old importer read.

**The YAML reader is the risk.** The kernel is stdlib only (AGENTS.md law 1), so it
reads the pools' YAML itself. A reader that gets the keys right and the values wrong
is worse than no reader: the charter and the check commands are what the department
*is*. So the reader is guarded against a reference parse of every pool file.
"""

import json
import shutil
from pathlib import Path

from holon import cli
from holon.execution import ventures
from tests.helpers import KernelCase

POOLS = Path("/home/ishanp/.hermes/organism/ventures")
VENTURE = POOLS / "career-pipeline"


class YamlReader(KernelCase):
    """The reader the whole builder rests on."""

    def test_it_reads_every_pools_yaml_the_way_a_yaml_library_does(self):
        """The comparison: the kernel's reader against a real YAML library, on every
        pool's own files. Where the two differ, the kernel's reader is wrong."""
        import yaml
        checked = 0
        for pool in sorted(POOLS.iterdir()):
            if not pool.is_dir():
                continue
            for name in ("_org.yaml", "smoke.yaml"):
                f = pool / name
                if not f.exists():
                    continue
                mine = ventures._mini_yaml(f)
                ref = yaml.safe_load(f.read_text()) or {}
                self.assertEqual(sorted(mine), sorted(ref),
                                 f"{f}: the keys differ from a YAML library's parse")
                self._same(mine, ref, str(f))
                checked += 1
        self.assertGreaterEqual(checked, 10, "the corpus was actually read")

    def _same(self, mine, ref, where):
        """The values, compared deeply (whitespace aside: a YAML folded line is one
        value, and the join may differ by a space)."""
        if isinstance(ref, dict):
            self.assertIsInstance(mine, dict, f"{where}: expected a mapping")
            for k, v in ref.items():
                self.assertIn(k, mine, f"{where}: missing {k}")
                self._same(mine[k], v, f"{where}.{k}")
        elif isinstance(ref, list):
            self.assertIsInstance(mine, list, f"{where}: expected a list")
            self.assertEqual(len(mine), len(ref), f"{where}: a different number of items")
            for i, (a, b) in enumerate(zip(mine, ref)):
                self._same(a, b, f"{where}[{i}]")
        elif isinstance(ref, str):
            self.assertIsInstance(mine, str, f"{where}: expected a string, got {type(mine).__name__}")
            self.assertEqual(" ".join(mine.split()), " ".join(ref.split()),
                             f"{where}: the text differs")
        else:
            self.assertEqual(mine, ref, f"{where}: the value differs")

    def test_it_refuses_what_it_cannot_read_rather_than_guessing(self):
        """A silently misread charter is worse than a refused one: the reader raises on
        a shape it does not understand."""
        f = self.tmp / "weird.yaml"
        f.write_text("key: value\nthis line has no colon\n")
        with self.assertRaises(ValueError):
            ventures._mini_yaml(f)

    def test_a_block_scalar_is_folded_the_way_a_yaml_library_folds_it(self):
        """The pools write their long prose as block scalars (`|`, `>`, `>-`) — eleven files in
        the corpus use one, five of them the folded form. The reader had no branch for them at
        all: it read the marker itself (`>-`) as the value and folded the whole block as a plain
        continuation, so a venture's `purpose` arrived as the literal characters `>-` followed by
        its text. A charter read that way is worse than a refused one, because it imports.

        The corpus comparison (`test_it_reads_every_pools_yaml_the_way_a_yaml_library_does` above)
        catches this against the pools' real files; this guard is self-contained, so the rule
        holds on a checkout that has no pools at all."""
        import yaml
        text = (
            "services:\n"
            "- id: a\n"
            "  purpose: >-\n"
            "    Tier-A automated browsing for ATS application flows (Ashby etc). MUST be "
            "reached through a\n"
            "    single client module that handshakes on init\n"
            "  access: cli\n"
            "- id: b\n"
            "  purpose: |\n"
            "    line one\n"
            "    line two\n"
            "- id: c\n"
            "  purpose: |-\n"
            "    stripped\n"
        )
        f = self.tmp / "block.yaml"
        f.write_text(text)
        mine = ventures._mini_yaml(f)
        ref = yaml.safe_load(text)
        self.assertEqual(sorted(mine), sorted(ref))
        for item, want in zip(mine["services"], ref["services"]):
            self.assertEqual(item["purpose"], want["purpose"], item["id"])
        # the marker is a constructor, not a value
        self.assertNotIn(">-", mine["services"][0]["purpose"])
        # a literal block keeps its newlines and clips exactly one trailing one; `|-` strips it
        self.assertEqual(mine["services"][1]["purpose"], "line one\nline two\n")
        self.assertEqual(mine["services"][2]["purpose"], "stripped")
        # an empty block is the empty string, not the marker
        f2 = self.tmp / "empty.yaml"
        f2.write_text("key: |-\nother: 1\n")
        self.assertEqual(ventures._mini_yaml(f2), {"key": "", "other": 1})


class VentureBuilder(KernelCase):
    """M4. One command reads a venture's four artifacts."""

    def pool(self, tmp: Path) -> Path:
        """A scratch copy of a venture pool, so a test never writes to the real one."""
        dst = tmp / "venture"
        shutil.copytree(VENTURE, dst, ignore=shutil.ignore_patterns("data", "archive", "kb", "logs"))
        return dst

    def test_a_dry_run_reads_all_four_artifacts_and_writes_nothing(self):
        """The dry run is the promise: it shows what an import would do, and it changes
        nothing. `_org.yaml` (the charter), `cron-jobs/` (the protocols), `smoke.yaml`
        (the checks) and `scripts/` (the commands) are all read."""
        pool = self.pool(self.tmp)
        out = cli.venture_import(pool, "career", dry_run=True, k=self.k)
        self.assertEqual(out["dept"], "career")
        self.assertEqual(out["protocols"], 15, "career-pipeline's fifteen protocols")
        self.assertGreater(out["checks"], 0, "its smoke checks")
        self.assertTrue(out["charter"], "its charter")
        self.assertEqual(out["scripts"], len([s for s in (pool / "scripts").iterdir() if s.is_file()]),
                         "the pool's scripts are counted")
        self.assertFalse(any(d["slug"] == "career" for d in self.k.org.depts()),
                         "a dry run creates nothing")

    def test_the_imported_protocols_keep_their_skills(self):
        """The fidelity M1 bought: an imported routine carries the protocol's skills, so
        the pool's own protocol is not lost in the move."""
        pool = self.pool(self.tmp)
        out = cli.venture_import(pool, "career", dry_run=True, k=self.k)
        self.assertGreater(out["protocols_with_skills"], 0,
                           "the protocols carry their skills — 61 of 62 do in the real corpus")

    def test_an_apply_creates_the_department_and_leaves_the_routines_unapproved(self):
        """An import is not an approval, and a routine that reached the schedule
        unapproved would be the outside-reach import the kernel deliberately disables."""
        pool = self.pool(self.tmp)
        out = cli.venture_import(pool, "career", dry_run=False, k=self.k)
        self.assertTrue(any(d["slug"] == "career" for d in self.k.org.depts()),
                        "the department exists")
        self.assertEqual(self.k.routines(), [], "and no routine is scheduled yet")

    def test_the_outreach_check_sees_the_repaired_text(self):
        """The Chair's standing rule: job and freelance work never sends outreach. The
        outside-reach check is computed on the *repaired* text, so the rule sees the
        protocol's own words — and the dry run says which protocols reach outside."""
        pool = self.pool(self.tmp)
        out = cli.venture_import(pool, "career", dry_run=True, k=self.k)
        self.assertGreater(out["outside_reach"], 0,
                           "the pool's outreach protocols are seen as reaching outside")

    def test_the_smoke_checks_become_the_departments_own(self):
        """The pool's `smoke.yaml` is YAML; the kernel's `[smoke.dept]` is TOML. The
        translation is the work."""
        pool = self.pool(self.tmp)
        cli.venture_import(pool, "career", dry_run=False, k=self.k)
        text = self.home.config_file.read_text()
        self.assertIn("[smoke.dept.career]", text, "the pool's checks are the department's now")
        self.assertIn("cli-reachable", text, "and the first one is there")

    def test_the_scope_roots_are_the_pools_own_directories(self):
        """A department's reach is its directories: the pool's own `data/`, `kb/` and
        `templates/`, and nothing above them."""
        pool = self.pool(self.tmp)
        cli.venture_import(pool, "career", dry_run=False, k=self.k)
        text = self.home.config_file.read_text()
        self.assertIn("[scope.dept.career]", text)
        self.assertIn(str(pool), text, "the pool's own path is in its reach")

    def test_the_builder_reads_every_pool(self):
        """The builder is not specific to one pool: it reads all ten."""
        for pool in sorted(POOLS.iterdir()):
            if not pool.is_dir() or not (pool / "cron-jobs").is_dir():
                continue
            v = ventures.read_venture(pool)
            self.assertTrue(v["protocols"], f"{pool.name}: its protocols are found")
            self.assertTrue(v["charter"], f"{pool.name}: its charter is found")
