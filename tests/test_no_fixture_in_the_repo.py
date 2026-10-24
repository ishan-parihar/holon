"""A test that plants its fixture must plant it out of the way.

Measured 2026-10-24, by running the suite and watching what it left behind.
`test_bylaws.Bylaws.test_the_reachable_command_question_does_not_depend_on_the_cwd` builds its
fixture with `Path(self.tmp.name) / "dept"`. `KernelCase.tmp` is already a `Path`, so `.name` is
its **final component** rather than the whole path: `Path("holon-test-abc")` is relative, and
`mkdir(parents=True)` then writes `./holon-test-abc/dept/bin/tool` into whatever the cwd is --
which, for a suite run from the repository root, is the repository itself. Every assertion passed
throughout, because the resolver genuinely is cwd-independent, so the only signal was the
directory itself.

The test's own docstring read "With `bin/tool` present in the repo root", which is why the leak
survived: it looks like the intent. It is not -- the intent is a relative command that must not
resolve, and the file belongs beside every other fixture the suite makes, in the temporary
directory.

This guard does not rewrite that test. It states the invariant it broke: a test may create
anything in the temp directory and nothing in the repo, and the check is done by running the
offending test in a working directory of the guard's own and measuring both that directory and
the repository root. A relative `Path` whose `mkdir` lands in the tree is caught whatever file it
is in.

The owning session fixed the line while this guard was being written (their commit changed
`Path(self.tmp.name) / "dept"` to `self.tmp / "dept"`), so both halves pass today -- which is the
point of writing them as measured checks rather than as an assertion about a file. The second
guard reads the shape out of the suite's own AST, so the day someone writes it again, in any
file, the suite says so before the directory appears.
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class NoFixtureInTheRepo(unittest.TestCase):
    def test_the_cwd_flip_test_plants_its_fixture_outside_the_repository(self):
        """The cwd-flip test's fixture is relative, so it lands in the cwd. Run it in a working
        directory of this guard's own and measure both that directory and the repository root:
        neither may gain a directory, or a test has planted its fixture somewhere it will rot.

        Counted rather than spotted, because the leak's shape is stable: `holon-test-<run>/`
        with a `dept/bin/tool` inside it, one per run.

        **Why the cwd is controlled rather than the aftermath cleaned up.** The defect is a
        relative `Path` resolving against the cwd, so the cwd is the mechanism. Three earlier
        versions of this guard tried to clean up after the fact and each was measured and
        rejected: (1) removing everything that appeared during the subprocess deleted a
        *concurrent* session's fixture too, because another session was running the same suite in
        this same tree (verified with `ps`) and writes the identical directory; (2) filtering by
        content (`dept/bin/tool`) separated nothing, since it is the same test writing the same
        shape; (3) sniffing the subprocess's stdout for the directory name found nothing at all,
        because the leaked test prints no identifier -- that version was worse than the defect,
        reporting OK with a leaked directory left behind. Running the test in a directory the
        guard created makes all three moot: the repository is only read, never written.

        The sandbox is named per run, because a fixed name is shared by two concurrent workers and
        one then sees the other's fixture in its own before/after (measured with a leftover
        planted by a simulated second worker)."""
        def planted(where):
            return sorted(p.name for p in Path(where).glob("holon-test-*") if p.is_dir())

        # a private working directory of this run's own, removed by cleanup
        sandbox = Path(tempfile.mkdtemp(prefix=f"fixture-guard-{REPO.name}-"))
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)

        repo_before = planted(REPO)
        sandbox_before = planted(sandbox)
        proc = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_bylaws.Bylaws.test_the_reachable_command_question_does_not_depend_on_the_cwd"],
            cwd=str(sandbox), capture_output=True, text=True, timeout=900,
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])

        # The repository root is the thing that matters: measured on its own, and it may not gain
        # a directory whatever the suite does.
        self.assertEqual(repo_before, planted(REPO),
                         "the repository root gained a directory: whatever the suite planted there "
                         "is not cleaned up")
        # And the defect is still observable where the relative path now resolves: a fixture in
        # the working directory means the path is still relative.
        self.assertEqual(sandbox_before, planted(sandbox),
                         "a test planted its fixture in the working directory it was run in: "
                         f"{sorted(set(planted(sandbox)) - set(sandbox_before))} appeared. A Path "
                         "built from `.name` is relative, so it lands in the cwd -- build it from "
                         "the temporary directory instead, and clean up in tearDown")

    def test_no_path_in_the_suite_is_built_from_a_directory_name(self):
        """The defect is one shape: `Path(some_tmp.name)`. `KernelCase.tmp` is already absolute,
        so `.name` takes the final component and throws the rest away -- and a relative Path
        resolves against the cwd rather than the temp dir. Nothing in the suite may do it.

        The scan reads code, not prose: this file discusses the shape to name it, so it is
        excluded from its own check, and the parse is an AST walk rather than a pattern that would
        match the sentence describing the pattern."""
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
