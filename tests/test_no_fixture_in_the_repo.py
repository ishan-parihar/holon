"""A test that plants its fixture must plant it out of the way.

Measured 2026-10-24, by running the suite and watching what it left behind.
`test_bylaws.Bylaws.test_the_reachable_command_question_does_not_depend_on_the_cwd` builds its
fixture with `Path(self.tmp.name) / "dept"`. `KernelCase.tmp` is already a `Path`, so `.name` is
its **final component** rather than the whole path: `Path("holon-test-abc")` is relative, and
`mkdir(parents=True)` then writes `./holon-test-abc/dept/bin/tool` into whatever the cwd is --
which, for a suite run from the repository root, is the repository itself. The assertions pass
(the resolver is genuinely cwd-independent) and the directory is never removed, so every run
leaves one more at the repo root: four accumulated in under fifteen minutes.

The test's own docstring reads "With `bin/tool` present in the repo root", which is why the
leak survived: it looks like the intent. It is not -- the intent is a relative command that
must not resolve, and the file belongs beside every other fixture the suite makes, in the
temporary directory.

This guard does not rewrite that test. It states the invariant it broke: a test may create
anything in the temp directory and nothing in the repo, and the check is done by running the
offending test in a subprocess and measuring the repository root before and after. A relative
`Path` whose `mkdir` lands in the tree is caught whatever file it is in.
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class NoFixtureInTheRepo(unittest.TestCase):
    def test_the_cwd_flip_test_plants_its_fixture_outside_the_repository(self):
        """The cwd-flip test's fixture is relative, so it lands in the cwd. Run it and measure
        the repo root: it must be byte-identical afterwards, or a test has planted its fixture
        in the tree the whole distribution lives in.

        Counted rather than spotted, because the leak's shape is stable: `holon-test-<run>/`
        with a `dept/bin/tool` inside it, one per run.

        It then removes what appeared, and that is not tidiness -- it is the difference between
        a guard and a leak. Running the offending test is what plants the directory, so a guard
        that measured the root and reported would *create* one more every time the suite ran,
        which is the defect it names. The measurement is the evidence; the removal is the
        cleanup the offending test does not do itself."""
        import shutil

        def planted():
            return sorted(p.name for p in REPO.glob("holon-test-*") if p.is_dir())

        before = planted()
        proc = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_bylaws.Bylaws.test_the_reachable_command_question_does_not_depend_on_the_cwd"],
            cwd=str(REPO), capture_output=True, text=True, timeout=900,
        )
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])
        # asserted BEFORE the cleanup, or the cleanup hides the very thing it measured
        after = planted()
        try:
            self.assertEqual(before, after,
                             "a test planted its fixture in the repository root: "
                             f"{sorted(set(after) - set(before))} appeared. A Path built from "
                             "`.name` is relative, so its mkdir lands in the cwd -- build it from "
                             "the temporary directory instead, and clean up in tearDown")
        finally:
            # whatever appeared does not stay: a guard that only reported would plant one more
            # every time the suite ran, which is the defect it names.
            for name in set(after) - set(before):
                shutil.rmtree(REPO / name, ignore_errors=True)

    def test_no_path_in_the_suite_is_built_from_a_directory_name(self):
        """The defect is one shape: `Path(some_tmp.name)`. `KernelCase.tmp` is already absolute,
        so `.name` takes the final component and throws the rest away -- and a relative Path
        resolves against the cwd rather than the temp dir. Nothing in the suite may do it.

        The scan reads code, not prose: this file discusses the shape to name it, so it is
        excluded from its own check, and docstring lines are skipped by the parse rather than by
        a pattern that would match the sentence describing the pattern."""
        import ast
        hits = []
        for path in sorted((REPO / "tests").glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Name) and func.id == "Path" and node.args):
                    continue
                arg = node.args[0]
                # Path(X.name) where X mentions the temp directory
                if (isinstance(arg, ast.Attribute) and arg.attr == "name"
                        and "tmp" in ast.dump(arg.value)):
                    hits.append(f"{path.name}:{node.lineno}")
        self.assertEqual([], hits,
                         "a relative Path is built from a temp directory's name: "
                         + ", ".join(hits) + ". Build it from the directory itself (it is "
                         "already absolute), and remove the fixture in tearDown")
