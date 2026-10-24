"""Tests for the reach's derivation from the pool's own `directories:` (A2, G1).

The department's reach was built from `POOL_DIRS`, a fixed tuple. Measured
2026-10-24 against `pioneer-qf`, the only pool in the portfolio that declares a
`directories:` key at all: it declares `sub-systems/`, `scratch/`, `bin/` and
`core/`, and the tuple names `data`, `logs` and `core` of them. So the reach's
list omitted three real working directories and named three that do not exist
there.

The reach still COVERED those directories, because `roots = [str(pool)] + roots`
prepends the pool root, which is what made this a precision gap rather than a
reach gap. The list, though, is what a reader audits, so a list naming six
directory names, half of them wrong, is itself the defect.

These guards are self-contained: each builds its own pool under a temp tree, and
one adds the real `pioneer-qf/_org.yaml` where it exists, so a checkout without
it still carries the rule.
"""

import os
from pathlib import Path

from holon.execution.ventures import POOL_DIRS, _declared_dirs, pool_roots, read_venture
from tests.helpers import KernelCase

# The real declaring pool. Skipped when absent, the same contract the status
# tests already use for their corpus. Measured location: this file sits at
# my-projects/holon-personal/tests/, and `Path.parents[2]` is my-projects, so
# pioneer-qf is a sibling of holon-personal. The env var still wins, which is
# what lets a checkout elsewhere point at its own tree.
PIONEER = Path(os.environ.get("HOLON_PIONEER_QF")
               or Path(__file__).resolve().parents[2] / "pioneer-qf")

ORG_YAML = """\
project:
  name: declaring-pool
directories:
  sub-systems/: {allow_any: true, require_agents: true}
  scratch/: {allow_any: true}
  bin/: {allow_any: true}
  core/:
    sub_dirs:
      decisions/: {suffix: .md, prefix: "PP-AD-"}
      regressions/: {suffix: .md, prefix: "PP-RG-"}
      worklog/: {suffix: .md}
"""


class ReachDerivation(KernelCase):
    """Which directories the reach names, and where that list comes from."""

    def _declaring_pool(self, name: str = "declaring") -> Path:
        pool = self.tmp / name
        for d in ("sub-systems", "scratch", "bin", "core"):
            (pool / d).mkdir(parents=True, exist_ok=True)
        (pool / "_org.yaml").write_text(ORG_YAML)
        return pool

    def _names(self, v: dict) -> list[str]:
        return [Path(r).name for r in pool_roots(v)]

    def _rel(self, v: dict) -> list[str]:
        """The reach as paths relative to the pool, which is the only form nesting is
        visible in: `core/decisions` and `core` have the same final component."""
        pool = Path(v["pool"])
        return [str(Path(r).relative_to(pool)) for r in pool_roots(v)]

    def test_the_pools_declared_shape_is_the_reach(self):
        """The declared list, not the fixed tuple. This pool declares four
        directories and the tuple would have named three that are absent."""
        v = read_venture(self._declaring_pool())
        self.assertEqual(self._names(v), ["sub-systems", "scratch", "bin", "core"],
                         "the reach names what the pool declares, in the pool's own order")

    def test_a_declaration_is_not_a_promise_the_directory_exists(self):
        """`files/:` is declared here but never created, so it must not enter the
        reach. A declaration describes a layout; only an existing directory is
        reachable."""
        pool = self._declaring_pool()
        (pool / "_org.yaml").write_text(ORG_YAML + "  files/: {allow_any: true}\n")
        v = read_venture(pool)
        self.assertNotIn("files", self._names(v),
                         "a declared-but-absent directory is not in the reach")

    def test_a_pool_that_declares_nothing_falls_back_to_the_tuple(self):
        """The fallback. `POOL_DIRS` is not deleted: most pools declare no
        `directories:` key at all, and mysterium (the other `_org.yaml` in the
        portfolio) is one of them."""
        pool = self.tmp / "bare"
        pool.mkdir(parents=True)
        for d in ("data", "logs", "references"):
            (pool / d).mkdir(parents=True)
        v = read_venture(pool)
        self.assertEqual(sorted(self._names(v)), ["data", "logs", "references"],
                         "the tuple's members that exist, since the pool declares none")

    def test_the_fallback_tuple_still_holds_references(self):
        """`references` is in `POOL_DIRS` and no pool's declared list names it. That
        asymmetry is precisely why the tuple is the fallback rather than dead
        config: a pool relying on it keeps `references/` in its reach."""
        self.assertIn("references", POOL_DIRS)
        pool = self.tmp / "refs"
        pool.mkdir(parents=True)
        (pool / "references").mkdir()
        v = read_venture(pool)
        self.assertIn("references", self._names(v))

    def test_sub_dirs_are_recursed_not_just_the_parent(self):
        """`core/` declares `decisions/`, `regressions/` and `worklog/` beneath it.
        A reach naming only `core` under-declares what the pool has, and the
        records live in those children, so they are named too."""
        pool = self._declaring_pool()
        for d in ("decisions", "regressions", "worklog"):
            (pool / "core" / d).mkdir(parents=True)
        v = read_venture(pool)
        rel = self._rel(v)
        for d in ("core", "core/decisions", "core/regressions", "core/worklog"):
            self.assertIn(d, rel, f"the declared child {d} is in the reach")
        # the nested path, not the bare name: `decisions` flat is what the first
        # version emitted, and it made `pool/core/decisions` unreachable
        self.assertIn("core/decisions", rel)
        self.assertNotIn("decisions", rel,
                         "a sub_dir is emitted nested under its parent, never flat")

    def test_the_real_pioneer_qf_shape(self):
        """The measurement this fix was made for, re-run against the real file.

        Skipped when the tree is absent, so the guard still runs everywhere. The
        first draft of this test asserted `data` was NOT in the reach, on a survey
        that had listed the pool's directories repo-relative and missed it; measured
        here, pioneer-qf declares 17 directories and every one exists, `data` among
        them. The old tuple named three of the 17 (`data`, `logs`, `core`).
        """
        if not (PIONEER / "_org.yaml").is_file():
            self.skipTest(f"no _org.yaml at {PIONEER}")
        v = read_venture(PIONEER)
        rel = self._rel(v)
        # every declared directory that exists is in the reach, not a subset of them
        declared: list[str] = []
        _declared_dirs(v["org"].get("directories"), declared)
        self.assertTrue(declared, "this pool declares a directories: key")
        for d in declared:
            self.assertIn(d, rel, f"{d} is declared by pioneer-qf's own _org.yaml")
        # the four the tuple's absence was reported against when this was filed
        for d in ("sub-systems", "scratch", "bin", "core"):
            self.assertIn(d, rel, f"{d} was missing from the POOL_DIRS-only reach")
        # the reach is the declared shape, so it must be broader than the fallback
        self.assertGreater(len(rel), len(POOL_DIRS),
                           "the declared shape is richer than the fixed fallback tuple")
        # every entry is a real directory: a reach naming an absent one is a lie
        for r in pool_roots(v):
            self.assertTrue(Path(r).is_dir(), f"{r} does not exist")


class DeclaredDirsParsing(KernelCase):
    """The reader itself, on the shape only: no filesystem involved."""

    def test_a_rule_only_entry_is_not_a_directory(self):
        """`check_records():` names a function, not a directory. The trailing slash
        is what `_org.yaml` uses to say "this one is", so a key without it is
        stepped over."""
        out: list[str] = []
        _declared_dirs({"core/": {"sub_dirs": {}}, "check_records()": None}, out)
        self.assertEqual(out, ["core"])

    def test_a_sub_dir_is_emitted_nested_not_flat(self):
        """`core/`'s child is `core/decisions`, not `decisions`. The first version
        emitted bare names, and `pool / "decisions"` does not exist, so every nested
        directory was silently unreachable — the flat name looked right in a test that
        checked final components only."""
        out: list[str] = []
        _declared_dirs({"core/": {"sub_dirs": {"decisions/": {}}}}, out)
        self.assertEqual(out, ["core", "core/decisions"])

    def test_a_non_mapping_key_is_ignored_not_fatal(self):
        """An org file whose `directories:` is a scalar or a list must not crash the
        importer: the fallback covers it, since a wrong reach is a lint finding
        while a traceback is an aborted import."""
        self.assertIsNone(_declared_dirs("nonsense", []))
        self.assertIsNone(_declared_dirs(["sub-systems/"], []))

    def test_nothing_declared_yields_an_empty_list(self):
        out: list[str] = []
        _declared_dirs(None, out)
        self.assertEqual(out, [])
