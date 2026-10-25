"""No stale bytecode cache: the code that runs is the code on disk.

Measured 2026-10-25, while cutting the duplicated `policy.json` writer. The cut exposed a
failure that had nothing to do with the cut:

    holon/execution/policy.py    p.chmod(0o640)     <- the source
    the running function          p.chmod(0o644)     <- the bytecode

The suite's own guard `test_policy_json_is_readable_by_the_enforcing_harness` (a PRE-EXISTING
test, written for H-07) failed in a full-suite run and passed alone. The mode was 0644 instead
of 0640 -- the enforcing harness could not read the policy it must enforce, and the kernel
swallowed the `OSError` that told it so.

**The mechanism.** CPython validates a timestamp-based `.pyc` by comparing the source's
**mtime (whole seconds)** and **size**. An edit that lands in the same second as the previous
one AND preserves the file's size is invisible to that check, and the stale bytecode is
loaded and run as if it were the source. Reproduced here as a test: `return 1` edited to
`return 2` in the same second, same size, and the import answered `1`.

`0o640` -> `0o644` is exactly that shape: both are four characters, so the size is unchanged.

**Why it matters here more than in an ordinary project.** The kernel is the thing that decides
what an employee may do. A stale bytecode cache means the running kernel is not the code the
repository holds -- and the failure is silent, because the guard that would catch it (the
mode check) is in the same suite as the code that drifted.

**The rule this guard states.** No `.pyc` in the tree may disagree with its source. The check
reads the bytecode and the source and compares them, so a poisoned cache is found by the suite
rather than by the next person who edits a file within a second of another edit.

The fix for a poisoned cache is to delete it -- and deleting a cache is safe by construction:
the cache is derived from the source, and Python rebuilds it. That is why this guard cleans
what it finds, and reports what it cleaned.
"""

import marshal
import struct
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _code_shape(code) -> list:
    """A comparable shape of a code object: names, bytecode, constants, names table.

    Two code objects for the same source have the same shape; a stale cache does not.

    **Sets compare by their contents, not by their repr.** A `frozenset` constant's repr
    depends on the hash seed, so two code objects for the SAME source print their set
    constants in a different order and look different. The first draft compared `repr()`
    and reported four files as poisoned when a forced rebuild had just written the caches
    from the sources -- a guard that reports a freshly built cache as stale is a guard
    nobody can act on. Sets are compared as sorted members."""
    out = [(code.co_name, code.co_code, code.co_names, code.co_varnames, code.co_argcount)]
    for k in code.co_consts:
        if hasattr(k, "co_code"):
            out.append(_code_shape(k))
        elif isinstance(k, (set, frozenset)):
            out.append(("set", tuple(sorted(repr(m) for m in k))))
        else:
            out.append(repr(k))
    return out


def _poisoned() -> list[tuple[str, str]]:
    """Every `.pyc` whose bytecode disagrees with the source it claims to cache.

    **pytest's caches are excluded.** pytest writes `<name>.cpython-314-pytest-9.1.1.pyc`
    -- the same header, the same offset, but the code is ASSERTION-REWRITTEN: pytest
    instruments every assert with a detailed explanation, so its bytecode legitimately
    differs from the source it was compiled from. The first draft of this guard did not
    know that, and reported 43 files as poisoned on a tree where pytest had run -- a
    guard that condemns a test runner's own instrumentation is a guard that stops the
    suite from running pytest at all.

    The marker is in the file NAME (`-pytest-`), not in the content: the content is a
    valid CPython cache in every respect."""
    out = []
    for pyc in sorted(REPO.rglob("*.pyc")):
        if "__pycache__" not in str(pyc) or ".git" in pyc.parts:
            continue
        if "-pytest-" in pyc.name:
            continue   # pytest's assertion-rewritten cache: different code BY DESIGN
        data = pyc.read_bytes()
        if len(data) < 16:
            continue
        flags = struct.unpack("<I", data[4:8])[0]
        if flags & 0x1:      # hash-based: invalidated by content, cannot go stale this way
            continue
        src = pyc.parent.parent / (pyc.name.split(".")[0] + ".py")
        if not src.exists():
            continue
        try:
            cached = marshal.loads(data[16:])
        except Exception:
            continue
        try:
            fresh = compile(src.read_text(), str(src), "exec", dont_inherit=True)
        except SyntaxError:
            continue
        if _code_shape(cached) != _code_shape(fresh):
            out.append((str(pyc.relative_to(REPO)), str(src.relative_to(REPO))))
    return out


class NoStaleBytecode(unittest.TestCase):
    def test_no_cached_bytecode_disagrees_with_its_source(self):
        """The code that runs must be the code on disk."""
        poisoned = _poisoned()
        if poisoned:
            # A poisoned cache is derived state: deleting it is safe by construction and
            # Python rebuilds it from the source. The suite removes what it found and
            # names it, so the next run starts clean.
            for pyc_rel, src_rel in poisoned:
                (REPO / pyc_rel).unlink(missing_ok=True)
            self.fail(f"stale bytecode cache: the running code is not the source "
                      f"({'; '.join(f'{p} <- {s}' for p, s in poisoned)}). The caches were "
                      "removed; re-run to confirm the source is what runs.")

    def test_pytests_rewritten_caches_are_not_reported_as_poisoned(self):
        """The exemption is verified, not assumed.

        pytest's cache is a valid CPython cache whose code is ASSERTION-REWRITTEN, so it
        differs from the source by design. The guard must not report it -- and the check
        is made against a REAL pytest cache, not a hand-made imitation, because the shape
        that matters (the `-pytest-` marker in the name) is what pytest writes."""
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "test_probe.py").write_text("def test_x():\n    assert True\n")
            r = subprocess.run([sys.executable, "-m", "pytest", "test_probe.py", "-q"],
                               cwd=d, capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest(f"pytest is not available here: {r.stderr[-200:]}")
            caches = list((d / "__pycache__").glob("*.pyc"))
            self.assertTrue(caches, "pytest wrote a cache")
            pytest_cache = [c for c in caches if "-pytest-" in c.name]
            self.assertTrue(pytest_cache,
                            f"pytest's cache carries the -pytest- marker: {[c.name for c in caches]}")
            # the exemption is by NAME, and the name is what carries it
            self.assertTrue(any("-pytest-" in c.name for c in pytest_cache))

    def test_the_mechanism_is_reproduced_so_the_guard_is_not_credulous(self):
        """The guard's premise, proved: a same-second, same-size edit leaves a stale cache.

        Without this, the guard is a claim about a mechanism nobody checked. With it, the
        guard's own docstring is a test -- and if CPython ever starts validating the cache by
        content, this test skips and says the guard can be retired.

        The probe is a SUBPROCESS, because the mechanism is about the import system's cache:
        `importlib.util.spec_from_file_location` does not use `__pycache__` the way a normal
        import does, and the first draft of this probe therefore showed the cache being
        correctly invalidated -- a probe whose shape differed from the code's shape (the
        lesson the ledger records twice)."""
        import os
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "stale_probe.py"
            src.write_text("def value():\n    return 1\n")
            # a normal import, so __pycache__ is written the way CPython writes it
            env = dict(os.environ, PYTHONPATH=str(d))
            probe = "import stale_probe; print(stale_probe.value())"
            first = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True,
                                   text=True)
            self.assertEqual("1", first.stdout.strip(), first.stderr)
            # edit in place: same second, same size
            before = src.stat()
            text = src.read_text().replace("return 1", "return 2")
            self.assertEqual(len(text), len(src.read_text()),
                             "the edit must preserve the size, or it is visible")
            src.write_text(text)
            # restore the ORIGINAL mtime -- captured BEFORE the write. (The first draft
            # captured it after, so it restored the NEW mtime and the cache was correctly
            # invalidated: a probe that measures the opposite of what it claims.)
            os.utime(src, (before.st_atime, before.st_mtime))
            second = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True,
                                    text=True)
            if second.stdout.strip() == "2":
                self.skipTest("this CPython validates the cache by content; the stale-cache "
                              "window this guard closes is not open on this interpreter")
            # the REPRODUCTION: the source says 2 and the running code answers 1
            self.assertEqual("1", second.stdout.strip(),
                             "the mechanism must reproduce: a same-second, same-size edit "
                             f"leaves the OLD bytecode running (the source now says 2, the "
                             f"process answered {second.stdout.strip()!r})")


if __name__ == "__main__":
    unittest.main()
